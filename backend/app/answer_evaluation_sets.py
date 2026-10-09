"""Frozen collections of saved controlled pairs; annotation coverage, not a judge."""
import copy
import json
from datetime import timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .answer_comparisons import comparison
from .answer_reviews import LABELS
from .db import AnswerEvaluationSet, PromptExperiment, get_db
from .governance import reject_credentials
from .prompts import fingerprint
from .security import audit, runtime_settings

router = APIRouter(prefix="/api/repositories/{repository_id}/answer-evaluation-sets")
Identity = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
SCOPE = "选定真实对照的固定样本集；只统计当前有效人工标注的覆盖情况。未标注、过期或来源变化不算正确，不计算质量胜者、胜率或模型准确率。读取和导出不会调用模型。"
MAX_PAYLOAD = 12_000_000


class SetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: Identity
    name: str = Field(min_length=1, max_length=80)
    note: str = Field(default="", max_length=1000)
    experiment_ids: list[Identity] = Field(min_length=1, max_length=20)

    @field_validator("name")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("样本集名称不能为空。")
        return value.strip()

    @field_validator("note")
    @classmethod
    def trim_note(cls, value):
        return value.strip()

    @field_validator("experiment_ids")
    @classmethod
    def unique_ids(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("同一对照组不能重复选择。")
        return sorted(value)


def safe_json(data, settings, status=200, export=False):
    encoded = json.dumps(data, ensure_ascii=False, default=str)
    reject_credentials(encoded, settings)
    headers = {"Content-Disposition": 'attachment; filename="devflow-answer-evaluation-set.json"'} if export else {}
    return JSONResponse(json.loads(encoded), status_code=status, headers=headers)


def existing(db, repository_id, request_id):
    return db.scalar(select(AnswerEvaluationSet).where(AnswerEvaluationSet.repository_id == repository_id, AnswerEvaluationSet.request_id == request_id))


def metadata(record):
    pairs = record.payload["pairs"]
    created_at = record.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return {key: getattr(record, key) for key in ("id", "name", "note", "fingerprint", "request_id", "created_by")} | {
        "created_at": created_at.astimezone(timezone.utc).isoformat(), "pair_count": len(pairs),
        "unique_inputs": len({p["comparison"]["left"]["context"]["input_hash"] for p in pairs})}


def identity(data):
    # New annotation versions and metrics do not change the saved answer/source.
    return {side: {key: data[side][key] for key in ("run_id", "task", "snapshot", "context")} for side in ("left", "right")}


def matching_retry(record, request_hash, actor):
    if record.request_hash != request_hash or record.created_by != actor:
        raise HTTPException(409, "该提交编号已用于其他样本集或提交者，请使用新的编号。")


def get_set(db, repository_id, set_id):
    record = db.get(AnswerEvaluationSet, set_id)
    if not record or record.repository_id != repository_id:
        raise HTTPException(404, "评估样本集不存在。")
    if fingerprint(record.payload) != record.fingerprint:
        raise HTTPException(409, "固定样本记录无法核对，请检查保存数据。")
    return record


@router.post("", status_code=201)
def create_set(repository_id: str, body: SetInput, request: Request, db=Depends(get_db), settings=Depends(runtime_settings)):
    request_hash = fingerprint(body.model_dump(exclude={"request_id"}))
    reject_credentials(json.dumps(body.model_dump(), ensure_ascii=False), settings)
    actor = request.state.actor["username"]
    saved = existing(db, repository_id, body.request_id)
    if saved:
        matching_retry(saved, request_hash, actor)
        return safe_json(metadata(get_set(db, repository_id, saved.id)), settings, status=201)
    pairs = []
    for ident in body.experiment_ids:
        experiment = db.get(PromptExperiment, ident)
        if not experiment or experiment.repository_id != repository_id:
            raise HTTPException(404, "所选对照组不存在。")
        data = comparison(db, repository_id, experiment.left_run_id, experiment.right_run_id)
        if not data["controlled_pair"]:
            raise HTTPException(409, "所选组未满足实际同源对照条件，不能加入固定样本集。")
        # Keep original answers, sources, provenance and recorded usage; labels remain versioned separately.
        frozen = json.loads(json.dumps(data, ensure_ascii=False, default=str))
        for side in ("left", "right"):
            frozen[side]["review"] = None
            frozen[side]["stale_review"] = False
        pairs.append({"experiment_id": ident, "frozen_hash": experiment.frozen_hash,
                      "identity_hash": fingerprint(identity(frozen)), "comparison": frozen})
    payload = {"schema": "answer-evaluation-set-snapshot-v1", "pairs": pairs}
    encoded = json.dumps(payload, ensure_ascii=False)
    if len(encoded.encode("utf-8")) > MAX_PAYLOAD:
        raise HTTPException(422, "样本内容超过 12 MB，请减少所选对照组。")
    reject_credentials(encoded, settings)
    record = AnswerEvaluationSet(repository_id=repository_id, request_id=body.request_id, request_hash=request_hash,
                                 name=body.name, note=body.note, fingerprint=fingerprint(payload), payload=payload, created_by=actor)
    try:
        db.add(record)
        db.flush()
        audit(db, request.state.actor, "answer_evaluation_set.create", record.id, repository_id,
              pairs=len(pairs), fingerprint=record.fingerprint)
        response = safe_json(metadata(record), settings, status=201)
        db.commit()
    except IntegrityError:
        db.rollback()
        record = existing(db, repository_id, body.request_id)
        if not record:
            raise HTTPException(409, "提交发生冲突，请使用原编号重试。") from None
        matching_retry(record, request_hash, actor)
        response = safe_json(metadata(get_set(db, repository_id, record.id)), settings, status=201)
    return response


@router.get("")
def list_sets(repository_id: str, db=Depends(get_db), settings=Depends(runtime_settings)):
    records = db.scalars(select(AnswerEvaluationSet).where(AnswerEvaluationSet.repository_id == repository_id)
                         .order_by(AnswerEvaluationSet.created_at.desc(), AnswerEvaluationSet.id.desc()).limit(20))
    return safe_json({"sets": [metadata(r) for r in records], "scope": SCOPE}, settings)


def detail(db, record):
    pairs, groups = [], {}
    eligible_pairs = fully_reviewed_pairs = excluded_fields = 0
    for frozen in record.payload["pairs"]:
        captured = frozen["comparison"]
        current = None
        reasons = []
        try:
            current = comparison(db, record.repository_id, captured["left"]["run_id"], captured["right"]["run_id"])
            if not current["controlled_pair"]:
                reasons = current["reasons"]
            if fingerprint(identity(current)) != frozen["identity_hash"]:
                reasons = [*reasons, "原回答、来源或版本与固定样本不一致。"]
        except HTTPException as exc:
            reasons = [str(exc.detail)]
        eligible = not reasons
        coverage, reviews = {}, {}
        for side in ("left", "right"):
            saved = captured[side]
            total = len(saved["snapshot"]["items"])
            review = current[side]["review"] if eligible else None
            annotations = review["annotations"] if review else []
            counts = {label: sum(a["decision"] == label for a in annotations) for label in LABELS}
            coverage[side] = {"fields": total, "reviewed": len(annotations), "unreviewed": total - len(annotations),
                              "counts": counts, "review_version": review["version"] if review else None,
                              "stale_review": bool(current and current[side]["stale_review"])}
            reviews[side] = review
            if not eligible:
                excluded_fields += total
                continue
            context = saved["context"]
            parameters = {k: context[k] for k in ("prompt_id", "model_config_hash", "role", "protocol_hash")}
            if "skill" in context:
                parameters["skill"] = context["skill"]
            key = fingerprint(parameters)
            group = groups.setdefault(key, {"id": key, "prompt_id": context["prompt_id"], "prompt_name": context["prompt_name"],
                                           "model_parameters": context["model_parameters"], "role": context["role"],
                                           "answers": 0, "fields": 0, "reviewed": 0, "unreviewed": 0,
                                           "counts": {label: 0 for label in LABELS}})
            group["answers"] += 1
            if "skill" in context:
                group["skill"] = context["skill"]
            group["fields"] += total
            group["reviewed"] += len(annotations)
            group["unreviewed"] += total - len(annotations)
            for label in LABELS:
                group["counts"][label] += counts[label]
        eligible_pairs += eligible
        fully_reviewed_pairs += eligible and all(c["unreviewed"] == 0 for c in coverage.values())
        pairs.append({**copy.deepcopy(frozen), "eligible": eligible, "reasons": reasons, "coverage": coverage, "current_reviews": reviews})
    return {"schema": "answer-evaluation-set-export-v1", "repository_id": record.repository_id, **metadata(record),
            "scope": SCOPE, "pairs": pairs,
            "coverage": {"eligible_pairs": eligible_pairs, "excluded_pairs": len(pairs) - eligible_pairs,
                         "fully_reviewed_pairs": fully_reviewed_pairs, "excluded_fields": excluded_fields,
                         "fields": sum(g["fields"] for g in groups.values()), "reviewed": sum(g["reviewed"] for g in groups.values()),
                         "unreviewed": sum(g["unreviewed"] for g in groups.values()), "by_prompt": sorted(groups.values(), key=lambda g: (g["prompt_id"], g["id"]))},
            "winner": None, "model_accuracy": None, "human_labels_generated": False}


@router.get("/{set_id}")
def set_detail(repository_id: str, set_id: Identity, db=Depends(get_db), settings=Depends(runtime_settings)):
    return safe_json(detail(db, get_set(db, repository_id, set_id)), settings)


@router.get("/{set_id}/export")
def export_set(repository_id: str, set_id: Identity, db=Depends(get_db), settings=Depends(runtime_settings)):
    return safe_json(detail(db, get_set(db, repository_id, set_id)), settings, export=True)
