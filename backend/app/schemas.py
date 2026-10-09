from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator
from .prompts import PromptId
from .task_skills import SkillId


class RepositoryInput(BaseModel):
    full_name: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", max_length=200)


class ChatInput(BaseModel):
    repository_id: str
    conversation_id: str | None = None
    message: str = Field(min_length=1, max_length=6000)
    task: Literal["chat", "issue", "pr", "ci", "workflow", "knowledge", "code", "report"] = "chat"
    target: int | None = Field(default=None, gt=0)
    prompt_id: PromptId = "baseline-v1"
    skill_id: SkillId | None = None


class Finding(BaseModel):
    severity: Literal["high", "medium", "low", "info"]
    title: str
    detail: str
    evidence_ids: list[str] = Field(default_factory=list)


class Analysis(BaseModel):
    title: str
    summary: str
    recommendation: str
    findings: list[Finding] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class DraftInput(BaseModel):
    run_id: str


class DocumentInput(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    path: str = Field(min_length=1, max_length=400)
    content: str = Field(min_length=1, max_length=100000)
    url: HttpUrl | None = None

    @field_validator("path")
    @classmethod
    def safe_path(cls, value):
        value = value.replace("\\", "/").strip()
        if value.startswith("/") or any(x in ("", ".", "..") for x in value.split("/")) or ":" in value:
            raise ValueError("请使用仓库内的相对文档路径，如 docs/release.md")
        return value

    @field_validator("title", "content")
    @classmethod
    def non_blank(cls, value):
        if not value.strip():
            raise ValueError("内容不能为空")
        return value


class SearchInput(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    mode: Literal["keyword", "hybrid"] = "keyword"
    top_k: int = Field(default=5, ge=1, le=20)

    @field_validator("query")
    @classmethod
    def non_blank(cls, value):
        if not value.strip():
            raise ValueError("查询不能为空")
        return value.strip()


class WorkspaceSyncInput(BaseModel):
    source: Literal["github", "local_project"] = "github"


class CodeSearchInput(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    path_prefix: str = Field(default="", max_length=400)
    top_k: int = Field(default=8, ge=1, le=20)
