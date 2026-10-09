"""Server-owned declarative analysis workflows. No scripts or uploaded skills."""
import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SkillId = Literal["code-explain-v1", "pr-review-v1", "ci-debug-v1", "release-check-v1"]
ToolName = Literal["get_repo_health", "inspect_issue", "inspect_pr", "inspect_ci", "search_knowledge", "search_memories", "search_code", "read_code"]
ROOT = Path(__file__).resolve().parents[1] / "skills"
MANIFEST = {
    "code-explain-v1": ("code", ["code"], False),
    "pr-review-v1": ("pr", ["pr"], True),
    "ci-debug-v1": ("ci", ["ci"], True),
    "release-check-v1": ("workflow", ["Planner", "code", "pr", "ci", "issue", "knowledge", "report", "Synthesis"], False),
}


class Skill(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: SkillId
    name: str = Field(min_length=1, max_length=80)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    description: str = Field(min_length=1, max_length=500)
    task: Literal["code", "pr", "ci", "workflow"]
    requires_target: bool
    roles: list[str] = Field(min_length=1, max_length=8)
    allowed_tools: list[ToolName] = Field(min_length=1, max_length=8)
    checklist: list[str] = Field(min_length=1, max_length=8)
    instructions: str = Field(min_length=30, max_length=4000)

    @model_validator(mode="after")
    def fixed_scope(self):
        if (self.task, self.roles, self.requires_target) != MANIFEST[self.id]:
            raise ValueError("Skill 任务范围与固定注册表不一致。")
        if len(set(self.allowed_tools)) != len(self.allowed_tools) or any(not x.strip() or len(x) > 240 for x in self.checklist):
            raise ValueError("Skill 工具或检查项无效。")
        return self


def load_skill(ident):
    if ident not in MANIFEST:
        raise ValueError("不支持的任务 Skill。")
    try:
        raw = (ROOT / (ident + ".json")).read_bytes()
        if len(raw) > 16000:
            raise ValueError("too large")
        skill = Skill.model_validate_json(raw)
        if skill.id != ident:
            raise ValueError("identity mismatch")
        return skill
    except (OSError, ValueError) as exc:
        raise ValueError("任务 Skill 定义不可用，请检查服务端配置。") from exc


def binding_for(skill):
    encoded = json.dumps(skill.model_dump(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return {"id": skill.id, "version": skill.version, "definition_hash": hashlib.sha256(encoded).hexdigest()}


def skill_binding(ident):
    return binding_for(load_skill(ident))


def task_definition(settings):
    if settings.analysis_skill_id is None:
        return None
    return settings._task_skill or load_skill(settings.analysis_skill_id)


def validate_selection(ident, task, target, definition=None):
    if ident is None:
        return
    skill = definition or load_skill(ident)
    if task != skill.task:
        raise ValueError("任务类型与所选 Skill 不一致。")
    if skill.requires_target and (type(target) is not int or target <= 0):
        raise ValueError("此 Skill 需要明确填写 PR 编号或 CI run ID。")


def skill_instructions(ident, role, definition=None):
    if ident is None:
        return ""
    skill = definition or load_skill(ident)
    if role not in skill.roles:
        raise ValueError("分析角色不属于所选 Skill。")
    return "\n任务 Skill：" + json.dumps({**binding_for(skill), "name": skill.name, "checklist": skill.checklist,
        "instructions": skill.instructions}, ensure_ascii=False) + "\n检查项是分析要求；缺少证据时明确记录缺口，不得声称已全部完成。"


def catalog():
    return [{**skill.model_dump(), **binding_for(skill)} for skill in (load_skill(ident) for ident in MANIFEST)]


def result_metadata(ident, mode, definition=None):
    skill = definition or load_skill(ident)
    return {**binding_for(skill), "name": skill.name, "checklist": skill.checklist,
            "mode": "demo-rules" if mode == "demo" else "live"}
