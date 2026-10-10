"""Composition root: constructs and owns long-lived services."""

from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Any

from orchestra.agents.reasoner import AnthropicReasoner, Reasoner
from orchestra.config import RunMode, Settings
from orchestra.db.models import Repository
from orchestra.db.session import Database
from orchestra.demo.host import DemoCodeHost
from orchestra.integrations.github.client import GitHubClient
from orchestra.integrations.github.protocol import CodeHost
from orchestra.integrations.mcp.manager import McpManager, load_config
from orchestra.integrations.sandbox import DockerSandbox
from orchestra.memory.store import MemoryStore
from orchestra.observability import get_logger
from orchestra.security.redaction import register_secret
from orchestra.settings_store import SettingsStore
from orchestra.tools.builtin import register_builtin_tools
from orchestra.tools.executor import ToolExecutor
from orchestra.tools.registry import ToolRegistry
from orchestra.workflows.events import EventBus, EventRecorder
from orchestra.workflows.runner import WorkflowRunner

log = get_logger(__name__)


class Services:
    def __init__(
        self, settings: Settings, *, demo_host: DemoCodeHost | None = None, reasoner: Reasoner | None = None
    ) -> None:
        self.settings = settings
        for secret in (
            settings.admin_token,
            settings.approver_token,
            settings.operator_token,
            settings.viewer_token,
            settings.anthropic_api_key,
            settings.github_token,
        ):
            register_secret(secret.get_secret_value() if secret else None)
        self.db = Database(settings.database_url)
        self.bus = EventBus()
        self.events = EventRecorder(self.db, self.bus)
        self.registry = ToolRegistry()
        register_builtin_tools(self.registry, sandbox_timeout_s=settings.sandbox_timeout_s)
        self.executor = ToolExecutor(self.db, self.registry, self.events, default_timeout_s=settings.tool_timeout_s)
        self.memory = MemoryStore(
            self.db,
            retention_days=settings.memory_retention_days,
            max_entries_per_repo=settings.memory_max_entries_per_repo,
        )
        self.store = SettingsStore(self.db, settings)
        self.mcp = McpManager(self.registry, self.executor.policy, load_config(settings.mcp_config_path))
        self.demo_host = demo_host or DemoCodeHost()
        self._reasoner_override = reasoner
        self._github: GitHubClient | None = None
        self._github_token: str | None = None
        self.runner = WorkflowRunner(self)
        self.checkpointer: Any = None
        self._stack = AsyncExitStack()

    async def startup(self) -> None:
        if self.settings.auto_create_schema:
            await self.db.create_all()
        await self.store.load()
        self.checkpointer = await self._open_checkpointer()
        await self.mcp.start()
        await self.memory.purge_expired()
        await self.runner.recover()

    async def shutdown(self) -> None:
        await self.runner.shutdown()
        await self.mcp.stop()
        if self._github is not None:
            await self._github.aclose()
        await self._stack.aclose()
        await self.db.dispose()

    async def _open_checkpointer(self) -> Any:
        url = self.settings.resolved_checkpoint_url
        if url.startswith(("postgres://", "postgresql://")):
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

            saver = await self._stack.enter_async_context(AsyncPostgresSaver.from_conn_string(url))
            await saver.setup()
            return saver
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        if url != ":memory:":
            self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        return await self._stack.enter_async_context(AsyncSqliteSaver.from_conn_string(url))

    # -- per-request factories ----------------------------------------------------------

    def github(self) -> GitHubClient | None:
        token = self.store.secret("github_token")
        if not token:
            return None
        if self._github is None or token != self._github_token:
            self._github = GitHubClient(
                token, base_url=self.settings.github_api_url, timeout_s=self.settings.github_timeout_s
            )
            self._github_token = token
        return self._github

    def host_for(self, repo: Repository) -> CodeHost:
        if repo.provider == "demo":
            return self.demo_host
        client = self.github()
        if client is None:
            raise RuntimeError("GitHub is not connected; add a token in Settings")
        return client

    def reasoner(self) -> Reasoner | None:
        if self._reasoner_override is not None:
            return self._reasoner_override
        if self.settings.mode is RunMode.DEMO:
            return None
        key = self.store.secret("anthropic_api_key")
        if not key:
            return None
        model = self.store.effective().model
        return AnthropicReasoner(
            key,
            model=model.model,
            effort=model.effort,
            max_tokens=self.settings.llm_max_tokens,
            timeout_s=self.settings.llm_timeout_s,
            max_retries=self.settings.llm_max_retries,
            fallbacks=self.settings.llm_fallbacks,
        )

    def sandbox(self) -> DockerSandbox | None:
        if not self.store.effective().preferences.sandbox_enabled:
            return None
        return DockerSandbox(
            self.settings.sandbox_image,
            cpus=self.settings.sandbox_cpus,
            memory=self.settings.sandbox_memory,
            timeout_s=self.settings.sandbox_timeout_s,
        )
