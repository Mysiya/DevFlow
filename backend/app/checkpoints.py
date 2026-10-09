"""Application task checkpoints in SQL. This is not a LangGraph BaseCheckpointSaver."""
import asyncio
import copy
import json

from sqlalchemy import update

from .db import AgentRun, RunCheckpoint, RunJob, SessionLocal, new_id, utcnow
from .knowledge import digest
from .planning import Plan
from .prompts import freeze_binding, bound_settings

TERMINAL_TASKS = {"completed", "partial", "failed", "skipped"}
RESUMABLE_RUNS = {"cancelled", "interrupted", "failed"}
MAX_STATE_BYTES = 16_000_000


class CheckpointError(RuntimeError):
    pass


def model_key(settings):
    # Credentials are deliberately excluded; rotating a key does not change the provider identity.
    return digest(json.dumps([settings.devflow_mode, settings.llm_base_url, settings.llm_model, settings.llm_reasoning_effort]))


def bounded_state(state):
    encoded = json.dumps(state, ensure_ascii=False)
    if len(encoded.encode()) > MAX_STATE_BYTES:
        raise CheckpointError("恢复点超过 16 MB 上限，请缩小仓库资料范围后重新分析。")
    return json.loads(encoded)


def create_checkpoint(db, run, snapshot, workspace, corpus, target, history, settings, memories=None):
    state = bounded_state({
        "input": {"repository_id": run.repository_id, "mode": run.mode, "model_key": model_key(settings),
                  "snapshot": {k: v for k, v in snapshot.items() if not k.startswith("_")},
                  "workspace": workspace, "corpus": corpus, "target": target, "history": history, "memories": memories or [], "analysis_binding": freeze_binding(settings),
                  "policy": {"budget": settings.workflow_max_tasks, "max_replans": settings.max_replans,
                             "concurrency": settings.agent_concurrency, "timeout": settings.agent_timeout_seconds,
                             "max_resumes": settings.max_resume_attempts}},
        "phase": "planning", "plans": [], "outcomes": {}, "context": None,
        "replan_attempted": False, "notices": [], "answer": None,
    })
    checkpoint = RunCheckpoint(run_id=run.id, state=state, lease_token=new_id())
    db.add(checkpoint)
    return checkpoint


def validate_checkpoint(checkpoint, run):
    try:
        if checkpoint.schema_version != 1:
            raise ValueError()
        state, frozen = checkpoint.state, checkpoint.state["input"]
        if frozen["repository_id"] != run.repository_id or frozen["mode"] != run.mode:
            raise ValueError()
        policy = frozen["policy"]
        if not 2 <= policy["budget"] <= 8 or not 0 <= policy["max_replans"] <= 1 or not 0 <= policy["max_resumes"] <= 5:
            raise ValueError()
        if not 1 <= policy["concurrency"] <= 4 or not 10 <= policy["timeout"] <= 300:
            raise ValueError()
        ids = []
        for plan in state["plans"]:
            validated = Plan.model_validate({"reason": plan["reason"], "steps": plan["steps"]})
            ids.extend(x.id for x in validated.steps)
        if len(set(ids)) != len(ids) or len(ids) > policy["budget"]:
            raise ValueError()
        if not set(state["outcomes"]).issubset(ids) or any(x["status"] not in TERMINAL_TASKS for x in state["outcomes"].values()):
            raise ValueError()
        if state["phase"] not in ("planning", "executing", "recovering", "synthesizing", "ready", "done"):
            raise ValueError()
        if "workspace" not in frozen or not isinstance(frozen["corpus"], list):
            raise ValueError()
        if frozen["workspace"] and frozen["workspace"].get("repository_id") != run.repository_id:
            raise ValueError()
        for key, value in state["outcomes"].items():
            if value["id"] != key or not isinstance(value["evidence"], dict) or not isinstance(value["gaps"], list):
                raise ValueError()
            if not all(field in value for field in ("agent", "title", "query", "target", "depends_on")):
                raise ValueError()
        return state
    except (ValueError, KeyError, TypeError):
        raise CheckpointError("该恢复点格式不受支持或不完整，请重新发起分析。") from None


def recovery_info(checkpoint, run, settings):
    if checkpoint is None:
        return None
    try:
        state = validate_checkpoint(checkpoint, run)
    except CheckpointError as exc:
        return {"available": False, "reason": str(exc), "phase": "invalid", "resume_count": checkpoint.resume_count,
                "settled_tasks": 0, "total_tasks": 0, "plans": [], "outcomes": [], "workspace": None}
    max_resumes = min(state["input"]["policy"]["max_resumes"], settings.max_resume_attempts)
    reason = "将继续原计划，复用已保存的任务结果、源码与文档版本。"
    available = run.status in RESUMABLE_RUNS
    if run.status == "completed": reason = "该运行已完成。"
    elif run.status == "running": reason = "该运行正在执行。"
    elif run.status == "queued": reason = "该运行已进入后台队列，请等待 Worker 执行。"
    elif checkpoint.resume_count >= max_resumes:
        available, reason = False, "已达到恢复次数上限，请重新发起分析。"
    elif run.mode != settings.devflow_mode or state["input"]["model_key"] != model_key(settings):
        available, reason = False, "运行模式或模型配置已变化，请恢复原配置或重新分析。"
    if available:
        try:
            bound_settings(settings, state["input"])
        except ValueError as exc:
            available, reason = False, str(exc)
    outcomes = [{k: x[k] for k in ("id", "agent", "title", "query", "target", "depends_on", "status", "gaps")} |
                {"summary": (x.get("analysis") or {}).get("summary", ""), "evidence_ids": list(x["evidence"])}
                for x in state["outcomes"].values()]
    return {"available": available, "reason": reason, "phase": state["phase"], "resume_count": checkpoint.resume_count,
            "max_resumes": max_resumes, "settled_tasks": len(outcomes),
            "total_tasks": sum(len(x["steps"]) for x in state["plans"]),
            "plans": state["plans"], "outcomes": outcomes, "workspace": state["input"]["workspace"]}


def claim_resume(db, run, checkpoint, settings, *, status="running", commit=True):
    info = recovery_info(checkpoint, run, settings)
    if not info or not info["available"]:
        raise CheckpointError(info["reason"] if info else "该记录未保存恢复点，请重新发起协作检查。")
    claimed = db.execute(update(AgentRun).where(AgentRun.id == run.id, AgentRun.status.in_(RESUMABLE_RUNS)).values(status=status))
    if claimed.rowcount != 1:
        db.rollback()
        raise CheckpointError("该运行已在执行或已完成，不能重复恢复。")
    checkpoint.lease_token = new_id()
    checkpoint.resume_count += 1
    checkpoint.updated_at = utcnow()
    if commit: db.commit()
    return checkpoint.lease_token


class CheckpointStore:
    def __init__(self, run_id, lease_token, job_lease=None):
        self.run_id, self.lease_token = run_id, lease_token
        self.lock = asyncio.Lock()
        self.job_lease = job_lease
        with SessionLocal() as db:
            saved = db.get(RunCheckpoint, run_id)
            run = db.get(AgentRun, run_id)
            self.state = copy.deepcopy(validate_checkpoint(saved, run))
            self.resume_count = saved.resume_count

    async def update(self, **changes):
        async with self.lock:
            state = bounded_state({**self.state, **changes})
            with SessionLocal() as db:
                if self.job_lease:
                    owned = db.execute(update(RunJob).where(RunJob.run_id == self.run_id, RunJob.status == "running", RunJob.lease_token == self.job_lease, RunJob.lease_until > utcnow()).values(updated_at=utcnow()))
                    if owned.rowcount != 1:
                        raise CheckpointError("后台任务的执行权已失效，已停止旧执行任务。")
                claimed = db.execute(update(RunCheckpoint).where(RunCheckpoint.run_id == self.run_id, RunCheckpoint.lease_token == self.lease_token).values(state=state, updated_at=utcnow()))
                if claimed.rowcount != 1:
                    raise CheckpointError("该运行的执行权已变化，已停止旧执行任务。")
                db.commit()
            self.state = state
