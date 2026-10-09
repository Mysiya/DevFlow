"""Versioned reviewer annotations over frozen real answers; no model judge."""
import copy
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .db import AgentRun, AnswerReview, get_db
from .fact_review import LEGACY_CHECKERS, apply_review, digest
from .governance import reject_credentials
from .security import audit, runtime_settings

router = APIRouter(prefix="/api/repositories/{repository_id}")
LABELS = ("supported", "conflict", "insufficient", "not_applicable")
SCOPE = "最近 30 次已完成分析的人工字段标注；只统计当前回答版本的最新评审，不代表模型准确率、全仓库质量或 Prompt A/B。"


class Annotation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: str = Field(min_length=1, max_length=50)
    decision: Literal["supported", "conflict", "insufficient", "not_applicable"]
    evidence_ids: list[str] = Field(default_factory=list, max_length=16)
    note: str = Field(min_length=1, max_length=1000)

    @field_validator("note")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("标注理由不能为空。")
        return value.strip()


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_version: int = Field(ge=0)
    annotations: list[Annotation] = Field(min_length=1, max_length=100)
    note: str = Field(default="", max_length=1000)


def snapshot_for(run):
    result = run.result or {}
    review = result.get("fact_review") or {}
    original = review.get("original_analysis")
    answer = original if review.get("checker") in LEGACY_CHECKERS and isinstance(original, dict) else result
    items = []
    for field in ("title", "summary", "recommendation"):
        if isinstance(answer.get(field), str) and answer[field].strip():
            items.append({"id": field, "kind": field, "text": answer[field], "evidence_ids": []})
    for index, finding in enumerate(answer.get("findings", [])):
        if not isinstance(finding, dict):
            raise HTTPException(409, "历史回答格式不支持评审。")
        items.append({"id": f"findings.{index}", "kind": "finding", "text": str(finding.get("title", "")) + "\n" + str(finding.get("detail", "")),
                      "evidence_ids": finding.get("evidence_ids", [])})
    for field in ("next_steps", "gaps"):
        for index, text in enumerate(answer.get(field, [])):
            items.append({"id": f"{field}.{index}", "kind": field, "text": str(text), "evidence_ids": []})
    evidence = copy.deepcopy(result.get("evidence", []))
    if not items or len(items) > 100 or len(evidence) > 128:
        raise HTTPException(409, "回答为空或超出单次评审范围，请离线整理。")
    value = {"schema": "answer-review-v1", "run_id": run.id, "question": run.question, "task": run.task,
             "mode": run.mode, "provider": result.get("provider", "unknown"), "items": items, "evidence": evidence}
    if len(json.dumps(value, ensure_ascii=False)) > 400000:
        raise HTTPException(409, "回答与证据超过评审长度上限，请离线整理。")
    return value


def completed_run(db, repository_id, run_id):
    run = db.get(AgentRun, run_id)
    if not run or run.repository_id != repository_id:
        raise HTTPException(404, "运行记录不存在。")
    if run.status != "completed" or not run.result:
        raise HTTPException(409, "只有已完成且保存回答的运行可以评审。")
    return run


def latest(db, repository_id, run_id):
    return db.scalar(select(AnswerReview).where(AnswerReview.repository_id == repository_id, AnswerReview.run_id == run_id).order_by(AnswerReview.version.desc()).limit(1))


def record_json(record):
    return {key: getattr(record, key) for key in ("id", "run_id", "version", "snapshot_hash", "annotations", "note", "created_by", "created_at")}


def automatic_for(run):
    result = run.result or {}
    return apply_review(result, {e["id"]: e for e in result.get("evidence", [])}).get("fact_review", {})


@router.get("/runs/{run_id}/answer-reviews")
def review_detail(repository_id: str, run_id: str, db=Depends(get_db), settings=Depends(runtime_settings)):
    run = completed_run(db, repository_id, run_id)
    snapshot = snapshot_for(run)
    reject_credentials(json.dumps(snapshot, ensure_ascii=False), settings)
    records = list(db.scalars(select(AnswerReview).where(AnswerReview.repository_id == repository_id, AnswerReview.run_id == run_id).order_by(AnswerReview.version.desc()).limit(20)))
    source_hash = digest(snapshot)
    current = records[0] if records and records[0].snapshot_hash == source_hash else None
    return {"snapshot": snapshot, "snapshot_hash": source_hash, "latest_version": records[0].version if records else 0,
            "current_review": record_json(current) if current else None, "records": [record_json(r) for r in records],
            "automatic_review": automatic_for(run), "scope": "标注原回答，自动移出的冲突原文也包含在内；自动核对只覆盖有限句式。历史列表最多显示 20 个版本。"}


def same_submission(record, body, annotations, actor):
    return bool(record and record.snapshot_hash == body.snapshot_hash and record.annotations == annotations and record.note == body.note.strip() and record.created_by == actor)


@router.get("/runs/{run_id}/answer-reviews/{review_id}")
def review_history(repository_id: str, run_id: str, review_id: str, db=Depends(get_db), settings=Depends(runtime_settings)):
    record = db.get(AnswerReview, review_id)
    if not record or record.repository_id != repository_id or record.run_id != run_id:
        raise HTTPException(404, "评审版本不存在。")
    data = {**record_json(record), "snapshot": record.snapshot, "automatic_review": record.automatic_review}
    reject_credentials(json.dumps(data, ensure_ascii=False, default=str), settings)
    return data


@router.post("/runs/{run_id}/answer-reviews", status_code=201)
def save_review(repository_id: str, run_id: str, body: ReviewInput, request: Request, db=Depends(get_db), settings=Depends(runtime_settings)):
    run = completed_run(db, repository_id, run_id)
    snapshot = snapshot_for(run)
    if digest(snapshot) != body.snapshot_hash:
        raise HTTPException(409, "回答或证据版本已变化，请重新加载后标注。")
    item_ids = {item["id"] for item in snapshot["items"]}
    evidence_ids = {item["id"] for item in snapshot["evidence"]}
    annotations = sorted([item.model_dump() for item in body.annotations], key=lambda item: item["item_id"])
    if len({item["item_id"] for item in annotations}) != len(annotations):
        raise HTTPException(422, "同一字段不能重复标注。")
    for item in annotations:
        if item["item_id"] not in item_ids or not set(item["evidence_ids"]) <= evidence_ids or len(set(item["evidence_ids"])) != len(item["evidence_ids"]):
            raise HTTPException(422, "标注字段或证据 ID 不属于当前回答。")
        if item["decision"] in ("supported", "conflict") and not item["evidence_ids"]:
            raise HTTPException(422, "支持或冲突标注须选取至少一项证据。")
    reject_credentials(json.dumps({"snapshot": snapshot, "annotations": annotations, "note": body.note}, ensure_ascii=False), settings)
    actor = request.state.actor["username"]
    existing = latest(db, repository_id, run_id)
    version = existing.version if existing else 0
    if body.expected_version in (version, version - 1) and same_submission(existing, body, annotations, actor):
        return record_json(existing)
    if body.expected_version != version:
        raise HTTPException(409, "评审已被更新，请重新加载后保存。")
    record = AnswerReview(repository_id=repository_id, run_id=run_id, version=version + 1, snapshot_hash=body.snapshot_hash,
                          snapshot=snapshot, annotations=annotations, note=body.note.strip(), created_by=actor, automatic_review=automatic_for(run))
    try:
        db.add(record)
        db.flush()
        audit(db, request.state.actor, "answer_review.save", record.id, repository_id, run_id=run_id, version=record.version, annotated=len(annotations), snapshot_hash=body.snapshot_hash)
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = latest(db, repository_id, run_id)
        if existing and existing.version == version + 1 and same_submission(existing, body, annotations, actor):
            return record_json(existing)
        raise HTTPException(409, "评审已被更新，请重新加载后保存。") from None
    return record_json(record)


def recent_samples(db, repository_id):
    runs = list(db.scalars(select(AgentRun).where(AgentRun.repository_id == repository_id, AgentRun.status == "completed", AgentRun.result.is_not(None)).order_by(AgentRun.created_at.desc(), AgentRun.id.desc()).limit(30)))
    ids = [run.id for run in runs]
    grouped = select(AnswerReview.run_id, func.max(AnswerReview.version).label("version")).where(AnswerReview.repository_id == repository_id, AnswerReview.run_id.in_(ids)).group_by(AnswerReview.run_id).subquery()
    reviews = {r.run_id: r for r in db.scalars(select(AnswerReview).join(grouped, (AnswerReview.run_id == grouped.c.run_id) & (AnswerReview.version == grouped.c.version)))}
    rows = []
    for run in runs:
        try:
            snapshot = snapshot_for(run)
        except HTTPException:
            rows.append({"run": run, "snapshot": None, "review": reviews.get(run.id), "current": False})
            continue
        record = reviews.get(run.id)
        rows.append({"run": run, "snapshot": snapshot, "review": record, "current": bool(record and record.snapshot_hash == digest(snapshot))})
    return rows


@router.get("/answer-reviews/status")
def review_status(repository_id: str, db=Depends(get_db)):
    rows = recent_samples(db, repository_id)
    counts = {key: 0 for key in (*LABELS, "unreviewed")}
    candidates = []
    for row in rows:
        snapshot, record, run = row["snapshot"], row["review"], row["run"]
        annotations = record.annotations if row["current"] else []
        for item in annotations:
            counts[item["decision"]] += 1
        item_count = len(snapshot["items"]) if snapshot else 0
        counts["unreviewed"] += item_count - len(annotations)
        candidates.append({"id": run.id, "question": run.question[:180], "created_at": run.created_at, "mode": run.mode,
                           "provider": (run.result or {}).get("provider", "unknown"), "item_count": item_count, "reviewed_count": len(annotations),
                           "latest_version": record.version if record else 0, "stale": bool(record and not row["current"]), "reviewable": bool(snapshot)})
    return {"scope": SCOPE, "runs": candidates, "counts": counts, "excluded_runs": sum(row["snapshot"] is None for row in rows),
            "stale_reviews": sum(bool(row["review"] and not row["current"]) for row in rows), "model_accuracy": None}


@router.get("/answer-reviews/export")
def export_reviews(repository_id: str, db=Depends(get_db), settings=Depends(runtime_settings)):
    samples = []
    for row in recent_samples(db, repository_id):
        if row["snapshot"] is None:
            continue
        record = row["review"]
        samples.append({"snapshot": row["snapshot"], "snapshot_hash": digest(row["snapshot"]), "review": record_json(record) if record else None,
                        "review_current": row["current"], "reviewed_snapshot": record.snapshot if record else None,
                        "automatic_review_at_save": record.automatic_review if record else None})
    data = {"schema": "answer-review-export-v1", "scope": SCOPE, "repository_id": repository_id, "samples": samples,
            "model_accuracy": None, "unreviewed_is_correct": False}
    encoded = json.dumps(data, ensure_ascii=False, default=str)
    reject_credentials(encoded, settings)
    return JSONResponse(json.loads(encoded), headers={"Content-Disposition": 'attachment; filename="devflow-answer-reviews.json"'})
