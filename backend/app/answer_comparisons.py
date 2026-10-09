"""Read-only comparisons of saved answers, with conservative Prompt eligibility."""
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from .answer_reviews import completed_run, latest, recent_samples, record_json, snapshot_for
from .db import get_db
from .fact_review import LEGACY_CHECKERS, digest
from .governance import reject_credentials
from .prompts import ANSWER_FIELDS, VARIANTS, catalog, fingerprint, system_prompt, template_hash
from .security import runtime_settings
from .telemetry import run_metrics
from .task_skills import skill_binding

router = APIRouter(prefix="/api/repositories/{repository_id}")
SCOPE = "保存的单次源码/知识分析对照；同问题、完整模型输入、证据和模型参数一致且 Prompt 不同时才满足对照条件。满足条件不代表质量更高；未进行人工判定，不计算胜率或模型准确率。"


def context_for(run):
    result = run.result or {}
    context = result.get("analysis_context")
    if not isinstance(context, dict) or context.get("schema") != "analysis-context-v1":
        return None, "未记录 Prompt 与完整输入版本"
    try:
        review = result.get("fact_review") or {}
        original = review.get("original_analysis")
        answer = original if review.get("checker") in LEGACY_CHECKERS and isinstance(original, dict) else result
        ids = context["evidence_ids"]
        evidence = {e["id"]: e for e in result.get("evidence", [])}
        skill = context.get("skill")
        skill_id = skill["id"] if skill is not None else None
        valid = (("skill" not in context or skill == skill_binding(skill_id))
                 and context["prompt_id"] in VARIANTS and context["template_hash"] == template_hash(context["prompt_id"])
                 and context["system_prompt"] == system_prompt(context["prompt_id"], context["role"], skill_id)
                 and context["system_hash"] == fingerprint(context["system_prompt"])
                 and context["model_config_hash"] == fingerprint(context["model_parameters"])
                 and context["model_parameters"]["mode"] == run.mode
                 and isinstance(ids, list) and len(ids) == len(set(ids))
                 and set(ids) <= set(evidence)
                 and context["evidence_hash"] == fingerprint({key: evidence[key] for key in ids})
                 and context["answer_hash"] == fingerprint({key: answer.get(key) for key in ANSWER_FIELDS})
                 and context["saved_answer_hash"] == fingerprint({key: answer.get(key) for key in (*ANSWER_FIELDS, "gaps")}))
        for key in ("input_hash", "question_hash", "protocol_hash"):
            valid = valid and isinstance(context[key], str) and len(context[key]) == 64 and all(c in "0123456789abcdef" for c in context[key])
        if not valid:
            return context, "保存的回答、证据或 Prompt 记录无法核对"
    except (KeyError, TypeError, ValueError):
        return context, "保存的 Prompt 记录不完整"
    return context, None


def side(db, run):
    snapshot = snapshot_for(run)
    review = latest(db, run.repository_id, run.id)
    current = bool(review and review.snapshot_hash == digest(snapshot))
    context, problem = context_for(run)
    return {"run_id": run.id, "created_at": run.created_at, "task": run.task, "snapshot": snapshot,
            "snapshot_hash": digest(snapshot), "context": context, "context_problem": problem,
            "review": record_json(review) if current else None, "stale_review": bool(review and not current),
            "metrics": run_metrics(db, run)}


def comparison(db, repository_id, left, right):
    if left == right:
        raise HTTPException(422, "请选择两次不同的运行。")
    a, b = (side(db, completed_run(db, repository_id, ident)) for ident in (left, right))
    reasons = []
    for label, item in (("左侧", a), ("右侧", b)):
        if item["context_problem"]:
            reasons.append(f"{label}：{item['context_problem']}。")
        if item["snapshot"]["mode"] != "live" or item["snapshot"]["provider"] != "llm":
            reasons.append(f"{label}不是完整的真实模型分析。")
        if item["task"] not in ("code", "knowledge"):
            reasons.append(f"{label}不是单次源码/知识分析；多 Agent 与工具决策不纳入此对照。")
    if a["snapshot"]["question"] != b["snapshot"]["question"] or a["task"] != b["task"]:
        reasons.append("问题或分析任务不同。")
    if fingerprint(a["snapshot"]["evidence"]) != fingerprint(b["snapshot"]["evidence"]):
        reasons.append("保存的证据内容或版本不同。")
    if not a["context_problem"] and not b["context_problem"]:
        for key, description in (("role", "分析角色"), ("protocol_hash", "分析协议"), ("question_hash", "模型收到的问题"),
                                 ("input_hash", "完整模型输入（含缺口与来源事实）"), ("model_config_hash", "模型服务或参数")):
            if a["context"][key] != b["context"][key]:
                reasons.append(f"{description}不同。")
        if a["context"]["prompt_id"] == b["context"]["prompt_id"]:
            reasons.append("两次运行使用同一 Prompt 版本。")
        if a["context"].get("skill") != b["context"].get("skill"):
            reasons.append("任务 Skill 或其定义版本不同。")
    return {"schema": "answer-comparison-v1", "repository_id": repository_id, "scope": SCOPE, "left": a, "right": b,
            "controlled_pair": not reasons, "reasons": reasons, "winner": None, "model_accuracy": None}


def safe_json(data, settings, export=False):
    encoded = json.dumps(data, ensure_ascii=False, default=str)
    reject_credentials(encoded, settings)
    headers = {"Content-Disposition": 'attachment; filename="devflow-answer-comparison.json"'} if export else {}
    return JSONResponse(json.loads(encoded), headers=headers)


@router.get("/analysis-prompts")
def prompt_catalog(repository_id: str):
    return {"prompts": catalog(), "default": "baseline-v1", "scope": "仅作用于最终结构化分析；Planner 和工具决策指令不变，演示模式不调用模型。"}


@router.get("/answer-comparisons/runs")
def comparison_runs(repository_id: str, db=Depends(get_db), settings=Depends(runtime_settings)):
    rows = []
    for sample in recent_samples(db, repository_id):
        run = sample["run"]
        context, problem = context_for(run)
        rows.append({"id": run.id, "question": run.question[:180], "task": run.task, "mode": run.mode, "created_at": run.created_at,
                     "provider": (run.result or {}).get("provider", "unknown"), "reviewable": sample["snapshot"] is not None,
                     "prompt_name": context.get("prompt_name") if context else None, "context_problem": problem})
    return safe_json({"runs": rows, "scope": SCOPE}, settings)


@router.get("/answer-comparisons")
def compare_answers(repository_id: str, left: str = Query(pattern=r"^[0-9a-f]{32}$"), right: str = Query(pattern=r"^[0-9a-f]{32}$"),
                    db=Depends(get_db), settings=Depends(runtime_settings)):
    return safe_json(comparison(db, repository_id, left, right), settings)


@router.get("/answer-comparisons/export")
def export_comparison(repository_id: str, left: str = Query(pattern=r"^[0-9a-f]{32}$"), right: str = Query(pattern=r"^[0-9a-f]{32}$"),
                      db=Depends(get_db), settings=Depends(runtime_settings)):
    return safe_json(comparison(db, repository_id, left, right), settings, export=True)
