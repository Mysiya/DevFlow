"""Atomically queue two analysis prompts against one frozen repository input."""
import copy
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .checkpoints import CheckpointError, bounded_state
from .db import AgentRun, Conversation, PromptExperiment, Repository, RunJob, get_db
from .governance import memory_corpus, reject_credentials
from .knowledge import corpus_hash, load_corpus
from .prompts import fingerprint, freeze_binding, model_parameters
from .run_queue import enqueue, request_stop
from .security import audit, runtime_settings
from .workspace import WorkspaceManager

router = APIRouter(prefix="/api/repositories/{repository_id}")
PROMPT_IDS = ("baseline-v1", "evidence-first-v2")
SCOPE = "一次提交两次真实模型分析，共用固定来源与参数；失败或中断不自动补发。满足对照条件不代表质量更高，仍需人工评审。"


class ExperimentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    question: str = Field(min_length=1, max_length=6000)
    task: Literal["code", "knowledge"] = "knowledge"

    @field_validator("question")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("问题不能为空。")
        return value.strip()


def existing(db, repository_id, request_id):
    return db.scalar(select(PromptExperiment).where(PromptExperiment.repository_id == repository_id, PromptExperiment.request_id == request_id))


def run_for(db, record, ident):
    run = db.get(AgentRun, ident)
    if not run or run.repository_id != record.repository_id:
        raise HTTPException(409, "对照来源运行缺失或不属于当前仓库。")
    job = db.get(RunJob, ident)
    problem = next((e["data"].get("message") for e in reversed(run.events or []) if e["type"] in ("run.failed", "run.interrupted", "run.cancelled")), None)
    return {"id": run.id, "status": run.status, "stop_requested": bool(job and job.stop_requested), "message": problem}


def record_json(db, record):
    left, right = (run_for(db, record, ident) for ident in (record.left_run_id, record.right_run_id))
    return {"id": record.id, "question": record.question, "task": record.task, "request_id": record.request_id,
            "frozen_hash": record.frozen_hash, "sources": record.sources, "parameters": record.parameters, "prompt_bindings": record.prompt_bindings,
            "left": left, "right": right, "active": any(r["status"] in ("queued", "running") for r in (left, right)),
            "answers_available": all(r["status"] == "completed" for r in (left, right)), "created_by": record.created_by, "created_at": record.created_at, "scope": SCOPE}


def safe_json(data, settings, status=200):
    encoded = json.dumps(data, ensure_ascii=False, default=str)
    reject_credentials(encoded, settings)
    return JSONResponse(json.loads(encoded), status_code=status)


def matching_retry(record, request_hash, actor):
    if record.request_hash != request_hash or record.created_by != actor:
        raise HTTPException(409, "该提交编号已用于其他问题、任务或提交者，请使用新的编号。")


@router.post("/prompt-experiments", status_code=202)
def create_experiment(repository_id: str, body: ExperimentInput, request: Request, db=Depends(get_db), settings=Depends(runtime_settings)):
    reject_credentials(body.question, settings)
    actor = request.state.actor["username"]
    request_hash = fingerprint({"question": body.question, "task": body.task})
    saved = existing(db, repository_id, body.request_id)
    if saved:
        matching_retry(saved, request_hash, actor)
        return safe_json(record_json(db, saved), settings, status=202)
    if settings.devflow_mode != "live" or not settings.llm_model or not settings.llm_api_key.get_secret_value():
        raise HTTPException(409, "两版对照需要配置真实模型；演示模式请查看已有规则分析。")
    repo = db.get(Repository, repository_id)
    if not repo or repo.snapshot.get("source") != "github":
        raise HTTPException(409, "当前仓库尚未具备真实模式的来源快照。")
    workspace = WorkspaceManager(settings).ref(repository_id)
    if body.task == "code" and not workspace:
        raise HTTPException(409, "源码对照需要先同步代码工作区。")
    corpus, memories = load_corpus(db, repository_id), memory_corpus(db, repository_id)
    try:
        frozen = bounded_state({"snapshot": {k: v for k, v in repo.snapshot.items() if not k.startswith("_")} |
                               {"synced_at": repo.synced_at.isoformat() if repo.synced_at else None},
                               "workspace": workspace, "corpus": corpus, "memories": memories, "target": None, "history": []})
    except CheckpointError as exc:
        raise HTTPException(409, str(exc)) from exc
    reject_credentials(json.dumps(frozen, ensure_ascii=False), settings)
    try:
        conversation = Conversation(repository_id=repository_id, title=body.question[:100])
        db.add(conversation)
        db.flush()
        runs, bindings = [], []
        for prompt_id in PROMPT_IDS:
            chosen = settings.model_copy(update={"analysis_prompt_id": prompt_id, "analysis_skill_id": None})
            binding = freeze_binding(chosen)
            run = AgentRun(repository_id=repository_id, conversation_id=conversation.id, question=body.question, task=body.task, mode="live")
            db.add(run)
            db.flush()
            enqueue(db, run, chosen, {**copy.deepcopy(frozen), "analysis_binding": binding})
            runs.append(run)
            bindings.append(binding)
        record = PromptExperiment(repository_id=repository_id, request_id=body.request_id, request_hash=request_hash,
                                  question=body.question, task=body.task, left_run_id=runs[0].id, right_run_id=runs[1].id,
                                  frozen_hash=fingerprint(frozen), sources={"workspace": workspace, "corpus_hash": corpus_hash(corpus),
                                  "document_chunks": len(corpus), "memory_hash": fingerprint(memories), "approved_memories": len(memories)},
                                  parameters=model_parameters(settings), prompt_bindings=bindings, created_by=actor)
        db.add(record)
        db.flush()
        audit(db, request.state.actor, "prompt_experiment.submit", record.id, repository_id,
              left_run_id=record.left_run_id, right_run_id=record.right_run_id, frozen_hash=record.frozen_hash)
        response = safe_json(record_json(db, record), settings, status=202)
        db.commit()
    except CheckpointError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    except IntegrityError:
        db.rollback()
        record = existing(db, repository_id, body.request_id)
        if not record:
            raise HTTPException(409, "提交发生冲突，请使用原提交编号重试。") from None
        matching_retry(record, request_hash, actor)
        response = safe_json(record_json(db, record), settings, status=202)
    return response


@router.get("/prompt-experiments")
def experiments(repository_id: str, db=Depends(get_db), settings=Depends(runtime_settings)):
    records = list(db.scalars(select(PromptExperiment).where(PromptExperiment.repository_id == repository_id).order_by(PromptExperiment.created_at.desc()).limit(20)))
    return safe_json({"records": [record_json(db, r) for r in records], "enabled": settings.devflow_mode == "live" and bool(settings.llm_model and settings.llm_api_key.get_secret_value()), "scope": SCOPE}, settings)


def get_experiment(db, repository_id, ident):
    record = db.get(PromptExperiment, ident)
    if not record or record.repository_id != repository_id:
        raise HTTPException(404, "对照记录不存在。")
    return record


@router.get("/prompt-experiments/{experiment_id}")
def experiment_detail(repository_id: str, experiment_id: str, db=Depends(get_db), settings=Depends(runtime_settings)):
    return safe_json(record_json(db, get_experiment(db, repository_id, experiment_id)), settings)


@router.post("/prompt-experiments/{experiment_id}/stop")
def stop_experiment(repository_id: str, experiment_id: str, request: Request, db=Depends(get_db), settings=Depends(runtime_settings)):
    record = get_experiment(db, repository_id, experiment_id)
    # Each stop is idempotent. Completed results remain intact.
    for ident in (record.left_run_id, record.right_run_id):
        run_for(db, record, ident)
        request_stop(db, db.get(AgentRun, ident))
    audit(db, request.state.actor, "prompt_experiment.stop", record.id, repository_id)
    db.commit()
    db.expire_all()
    return safe_json(record_json(db, record), settings)
