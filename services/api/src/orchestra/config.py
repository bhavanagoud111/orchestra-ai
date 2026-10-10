"""Runtime configuration.

All settings are read from environment variables prefixed with ``ORCHESTRA_``
(or a ``.env`` file). Secrets are wrapped in ``SecretStr`` so they never appear
in reprs, logs, or API responses.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RunMode(StrEnum):
    DEMO = "demo"
    LIVE = "live"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ORCHESTRA_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    environment: str = "development"
    host: str = "127.0.0.1"  # set ORCHESTRA_HOST=0.0.0.0 inside containers
    port: int = 8000
    mode: RunMode = RunMode.DEMO
    data_dir: Path = Path("./data")

    # Persistence
    database_url: str = "sqlite+aiosqlite:///./data/orchestra.db"
    # Separate store for LangGraph checkpoints. Defaults to a SQLite file under data_dir;
    # set to a postgresql:// URL to use the Postgres checkpointer.
    checkpoint_url: str | None = None
    # Create tables on startup (demo/dev). Production deployments run `alembic upgrade head` instead.
    auto_create_schema: bool = True

    # API authentication. Each token maps to a role. At least one must be set.
    admin_token: SecretStr | None = None
    approver_token: SecretStr | None = None
    operator_token: SecretStr | None = None
    viewer_token: SecretStr | None = None
    # Key used to encrypt secrets saved through the Settings API (Fernet, urlsafe base64).
    secret_key: SecretStr | None = None

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    rate_limit_per_minute: int = 240

    # LLM provider
    anthropic_api_key: SecretStr | None = None
    llm_model: str = "claude-opus-5-5"
    llm_effort: str = "high"
    llm_max_tokens: int = 16000
    llm_timeout_s: float = 300.0
    llm_max_retries: int = 3
    # Server-side refusal fallback (Anthropic beta). Disable if your account does not have it.
    llm_fallbacks: bool = True

    # GitHub
    github_token: SecretStr | None = None
    github_api_url: str = "https://api.github.com"
    github_oauth_client_id: str | None = None
    github_timeout_s: float = 20.0

    # Workflow limits
    max_graph_steps: int = 60
    workflow_timeout_s: float = 900.0
    node_timeout_s: float = 360.0
    max_concurrent_workflows: int = 4
    max_debug_revisions: int = 1
    tool_timeout_s: float = 30.0

    # Retrieval
    retrieval_max_files: int = 400
    retrieval_max_file_bytes: int = 120_000
    retrieval_top_k: int = 8
    context_char_budget: int = 60_000

    # Sandbox for test execution
    sandbox_enabled: bool = False
    sandbox_image: str = "orchestra-sandbox-python:3.12"  # see infrastructure/docker/sandbox-python.Dockerfile
    sandbox_command: str = "python -m pytest -q -p no:cacheprovider"
    sandbox_cpus: float = 1.0
    sandbox_memory: str = "512m"
    sandbox_timeout_s: int = 300

    # MCP servers (path to a JSON file, see docs/mcp.md)
    mcp_config_path: Path | None = None

    # Memory retention
    memory_retention_days: int = 90
    memory_max_entries_per_repo: int = 500

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str) and not value.startswith("["):
            return [v.strip() for v in value.split(",") if v.strip()]
        return value

    @property
    def is_demo(self) -> bool:
        return self.mode is RunMode.DEMO

    @property
    def resolved_checkpoint_url(self) -> str:
        return self.checkpoint_url or str(self.data_dir / "checkpoints.db")


@lru_cache
def get_settings() -> Settings:
    return Settings()
