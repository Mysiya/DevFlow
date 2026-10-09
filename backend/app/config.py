from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, PrivateAttr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from .prompts import PromptId
from .task_skills import SkillId, Skill


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    devflow_mode: Literal["demo", "live"] = "demo"
    database_url: str = "sqlite:///./data/devflow.db"
    github_token: SecretStr = SecretStr("")
    github_write_enabled: bool = False
    github_webhook_enabled: bool = False
    github_webhook_secret: SecretStr = SecretStr("")
    github_webhook_repository_ids: str = ""
    auth_enabled: bool = False
    auth_cookie_secure: bool = False
    bootstrap_admin_username: str = Field(default="admin", pattern=r"^[a-zA-Z0-9_.-]{3,40}$")
    bootstrap_admin_password: SecretStr = SecretStr("")
    llm_base_url: str = "https://api.example.com/v1"
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = ""
    analysis_prompt_id: PromptId = "baseline-v1"
    analysis_skill_id: SkillId | None = None
    _task_skill: Skill | None = PrivateAttr(default=None)
    llm_max_tokens: int = Field(default=4096, ge=256, le=32768)
    llm_reasoning_effort: Literal["none", "low", "high", "max"] | None = None
    llm_input_price_per_million: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    llm_output_price_per_million: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    llm_cached_input_price_per_million: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    llm_price_currency: str = Field(default="CNY", pattern=r"^[A-Z]{3}$")
    mcp_access_token: SecretStr = SecretStr("")
    mcp_repository_ids: str = ""

    @field_validator("llm_input_price_per_million", "llm_output_price_per_million", "llm_cached_input_price_per_million", mode="before")
    @classmethod
    def empty_price_is_unknown(cls, value):
        return None if value == "" else value

    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    max_agent_steps: int = 6
    workspace_root: str = "./data/code"
    workspace_max_files: int = Field(default=500, ge=10, le=2000)
    workspace_max_file_bytes: int = Field(default=256000, ge=1000, le=1000000)
    workspace_max_total_bytes: int = Field(default=8000000, ge=100000, le=32000000)
    local_project_enabled: bool = True
    workflow_max_tasks: int = Field(default=6, ge=2, le=8)
    agent_concurrency: int = Field(default=2, ge=1, le=4)
    agent_timeout_seconds: int = Field(default=120, ge=10, le=300)
    max_replans: int = Field(default=1, ge=0, le=1)
    max_resume_attempts: int = Field(default=3, ge=0, le=5)
    worker_concurrency: int = Field(default=2, ge=1, le=4)
    queue_poll_seconds: float = Field(default=0.5, ge=0.1, le=5)
    job_lease_seconds: int = Field(default=30, ge=15, le=120)
    retrieval_backend: Literal["keyword", "milvus"] = "keyword"
    chunk_size: int = Field(default=1000, ge=200, le=4000)
    chunk_overlap: int = Field(default=120, ge=0, le=180)
    embedding_base_url: str = "https://api.example.com/v1"
    embedding_api_key: SecretStr = SecretStr("")
    embedding_model: str = ""
    embedding_dimensions: int | None = Field(default=None, ge=2, le=65536)
    embedding_local: bool = False
    milvus_uri: str = "http://127.0.0.1:19530"
    milvus_deployment: Literal["standalone", "lite"] = "standalone"
    milvus_token: SecretStr = SecretStr("")
    milvus_collection: str = Field(default="devflow_knowledge", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,40}$")
    rerank_enabled: bool = False
    rerank_base_url: str = "https://api.jina.ai/v1"
    rerank_api_key: SecretStr = SecretStr("")
    rerank_model: str = ""

    @model_validator(mode="after")
    def local_services_stay_on_loopback(self):
        from urllib.parse import urlsplit
        for value,local in ((self.embedding_base_url,self.embedding_local),(self.milvus_uri,self.milvus_deployment=="lite")):
            if local:
                url=urlsplit(value)
                if url.scheme!="http" or url.hostname!="127.0.0.1" or url.username or url.password or url.query or url.fragment:
                    raise ValueError("本地向量服务仅允许固定回环 HTTP 地址，不接受外部主机或附带凭据的 URL。")
        if self.github_webhook_enabled:
            import re
            secret=self.github_webhook_secret.get_secret_value()
            ids=self.github_webhook_repository_ids.split(",")
            if self.devflow_mode!="live" or len(secret)<32 or not all(re.fullmatch(r"[0-9a-f]{32}",ident.strip()) for ident in ids):
                raise ValueError("启用 Webhook 需要 live 模式、独立至少 32 字符密钥与仓库 ID 白名单。")
            if secret in [value.get_secret_value() for value in (self.github_token,self.llm_api_key,self.embedding_api_key,self.mcp_access_token)]:
                raise ValueError("Webhook 密钥必须独立于其他连接密钥。")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
