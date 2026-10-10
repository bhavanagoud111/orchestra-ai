"""Runtime settings editable from the Settings page.

Environment variables provide defaults. Operators can override a small,
explicitly allowlisted set of values at runtime; secrets saved here are
encrypted with ORCHESTRA_SECRET_KEY and are never returned by the API.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from orchestra.config import Settings
from orchestra.db.models import SettingEntry, utcnow
from orchestra.db.session import Database
from orchestra.security.crypto import SecretBox
from orchestra.security.redaction import register_secret

SECRET_KEYS = ("anthropic_api_key", "github_token")


class ModelSettings(BaseModel):
    provider: Literal["anthropic"] = "anthropic"
    model: str = Field(min_length=1, max_length=100)
    effort: Literal["low", "medium", "high", "xhigh", "max"]


class LimitSettings(BaseModel):
    max_graph_steps: int = Field(ge=5, le=500)
    workflow_timeout_s: float = Field(gt=0, le=7200)
    node_timeout_s: float = Field(gt=0, le=3600)
    max_debug_revisions: int = Field(ge=0, le=5)
    max_concurrent_workflows: int = Field(ge=1, le=32)


class ToolSettings(BaseModel):
    disabled: list[str] = Field(default_factory=list)


class ExecutionPreferences(BaseModel):
    sandbox_enabled: bool
    require_tests_for_pr: bool = False


class EffectiveSettings(BaseModel):
    mode: str
    model: ModelSettings
    limits: LimitSettings
    tools: ToolSettings
    preferences: ExecutionPreferences


class SettingsUpdate(BaseModel):
    model: ModelSettings | None = None
    limits: LimitSettings | None = None
    tools: ToolSettings | None = None
    preferences: ExecutionPreferences | None = None
    secrets: dict[Literal["anthropic_api_key", "github_token"], str | None] | None = Field(
        default=None, description="Write-only. Set a value to store it, null to clear it."
    )


class SettingsStore:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.env = settings
        self.box = SecretBox(settings.secret_key.get_secret_value() if settings.secret_key else None)
        self._cache: dict[str, Any] = {}
        self._secrets: dict[str, str] = {}

    async def load(self) -> None:
        async with self.db.session() as s:
            rows = (await s.execute(SettingEntry.__table__.select())).mappings().all()
        self._cache = {r["key"]: r["value"] for r in rows if r["value"] is not None}
        self._secrets = {}
        for r in rows:
            if r["secret"] is not None and self.box.available:
                self._secrets[r["key"]] = self.box.decrypt(r["secret"])
                register_secret(self._secrets[r["key"]])

    def effective(self) -> EffectiveSettings:
        e = self.env
        return EffectiveSettings(
            mode=e.mode.value,
            model=ModelSettings(**{"model": e.llm_model, "effort": e.llm_effort, **self._cache.get("model", {})}),
            limits=LimitSettings(
                **{
                    "max_graph_steps": e.max_graph_steps,
                    "workflow_timeout_s": e.workflow_timeout_s,
                    "node_timeout_s": e.node_timeout_s,
                    "max_debug_revisions": e.max_debug_revisions,
                    "max_concurrent_workflows": e.max_concurrent_workflows,
                    **self._cache.get("limits", {}),
                }
            ),
            tools=ToolSettings(**self._cache.get("tools", {})),
            preferences=ExecutionPreferences(
                **{"sandbox_enabled": e.sandbox_enabled, **self._cache.get("preferences", {})}
            ),
        )

    def secret(self, key: str) -> str | None:
        if key in self._secrets:
            return self._secrets[key]
        env_value = getattr(self.env, key, None)
        return env_value.get_secret_value() if env_value else None

    def secret_status(self) -> dict[str, dict[str, Any]]:
        return {
            key: {
                "configured": self.secret(key) is not None,
                "source": "settings" if key in self._secrets else ("environment" if self.secret(key) else None),
            }
            for key in SECRET_KEYS
        }

    async def update(self, update: SettingsUpdate, actor: str) -> EffectiveSettings:
        async with self.db.session() as s:
            for key in ("model", "limits", "tools", "preferences"):
                value = getattr(update, key)
                if value is not None:
                    row = await s.get(SettingEntry, key) or SettingEntry(key=key)
                    row.value = value.model_dump(mode="json")
                    row.updated_by, row.updated_at = actor, utcnow()
                    s.add(row)
            for key, plaintext in (update.secrets or {}).items():
                row = await s.get(SettingEntry, key) or SettingEntry(key=key)
                row.secret = self.box.encrypt(plaintext) if plaintext else None
                row.updated_by, row.updated_at = actor, utcnow()
                s.add(row)
        await self.load()
        return self.effective()
