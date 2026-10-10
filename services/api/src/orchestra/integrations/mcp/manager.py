"""MCP (Model Context Protocol) tool integration.

Servers are declared in a JSON config file (``ORCHESTRA_MCP_CONFIG_PATH``)::

    {"servers": [{
        "name": "docs",
        "command": "python", "args": ["examples/mcp/docs_server.py"],
        "timeout_s": 15,
        "tools": {"search_docs": {"permission": "read", "agents": ["debugging"]}}
    }]}

Each discovered tool is registered as ``mcp.<server>.<tool>``. Tools that are not
declared under ``tools`` are registered as write-permission tools with no agent
grants, i.e. visible in the UI but unusable until an operator configures them.
Responses are validated against the tool's ``outputSchema`` when one is
published, and screened for prompt injection by the ToolExecutor.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent
from pydantic import BaseModel, Field

from orchestra.observability import get_logger
from orchestra.schemas.agents import AgentName
from orchestra.tools.executor import ToolError
from orchestra.tools.policy import ToolPolicy
from orchestra.tools.registry import Permission, ToolRegistry, ToolSpec

log = get_logger(__name__)


class McpToolGrant(BaseModel):
    permission: Permission = Permission.READ
    agents: list[AgentName] = Field(default_factory=list)


class McpServerConfig(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9_-]{1,32}$")
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None
    timeout_s: float = 15.0
    tools: dict[str, McpToolGrant] = Field(default_factory=dict)


class McpConfig(BaseModel):
    servers: list[McpServerConfig] = Field(default_factory=list)


def load_config(path: Path | None) -> McpConfig:
    if path is None or not path.exists():
        return McpConfig()
    return McpConfig.model_validate(json.loads(path.read_text()))


@dataclass
class _Server:
    config: McpServerConfig
    session: ClientSession | None = None
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None
    error: str | None = None
    tools: list[str] = field(default_factory=list)


class McpResult(BaseModel):
    is_error: bool
    text: list[str]
    structured: Any = None


class McpManager:
    def __init__(self, registry: ToolRegistry, policy: ToolPolicy, config: McpConfig) -> None:
        self.registry = registry
        self.policy = policy
        self.config = config
        self.servers: dict[str, _Server] = {}

    async def start(self, connect_timeout_s: float = 20.0) -> None:
        for cfg in self.config.servers:
            server = _Server(cfg)
            self.servers[cfg.name] = server
            server.task = asyncio.create_task(self._own(server), name=f"mcp:{cfg.name}")
        for server in self.servers.values():
            try:
                await asyncio.wait_for(server.ready.wait(), timeout=connect_timeout_s)
            except TimeoutError:
                server.error = server.error or "timed out connecting"
            if server.error:
                self.registry.unavailable[f"mcp:{server.config.name}"] = server.error
                log.warning("mcp server unavailable", extra={"server": server.config.name, "reason": server.error})

    async def stop(self) -> None:
        for server in self.servers.values():
            server.stop.set()
        tasks = [s.task for s in self.servers.values() if s.task]
        if tasks:
            await asyncio.wait(tasks, timeout=5)
        for server in self.servers.values():
            self.registry.remove_source(f"mcp:{server.config.name}")

    def status(self) -> list[dict[str, Any]]:
        return [
            {
                "name": s.config.name,
                "connected": s.session is not None and s.error is None,
                "error": s.error,
                "tools": s.tools,
            }
            for s in self.servers.values()
        ]

    async def _own(self, server: _Server) -> None:
        """Owns the server's transport and session for its whole lifetime (one task, one cancel scope)."""
        cfg = server.config
        params = StdioServerParameters(
            command=cfg.command,
            args=cfg.args,
            cwd=cfg.cwd,
            # Pass only an explicit minimal environment: never leak Orchestra's secrets to tool servers.
            env={"PATH": os.environ.get("PATH", ""), **cfg.env},
        )
        try:
            async with AsyncExitStack() as stack:
                read, write = await stack.enter_async_context(stdio_client(params))
                session = await stack.enter_async_context(ClientSession(read, write))
                await asyncio.wait_for(session.initialize(), timeout=cfg.timeout_s)
                listed = await asyncio.wait_for(session.list_tools(), timeout=cfg.timeout_s)
                server.session = session
                for tool in listed.tools:
                    self._register(server, tool)
                server.ready.set()
                await server.stop.wait()
        except Exception as exc:  # noqa: BLE001 - surfaced through status() and registry.unavailable
            server.error = f"{exc.__class__.__name__}: {exc}"[:300]
        finally:
            server.session = None
            server.ready.set()

    def _register(self, server: _Server, tool: Any) -> None:
        cfg = server.config
        name = f"mcp.{cfg.name}.{tool.name}"
        grant = cfg.tools.get(tool.name)
        permission = grant.permission if grant else Permission.WRITE
        output_schema = getattr(tool, "output_schema", None)

        async def handler(args: dict[str, Any], _runtime: Any) -> McpResult:
            if server.session is None:
                raise ToolError(f"MCP server {cfg.name} is not connected", transient=True)
            result = await server.session.call_tool(tool.name, args, read_timeout_seconds=cfg.timeout_s)
            if not hasattr(result, "content"):
                raise ToolError(f"MCP tool {tool.name} returned an unsupported result type")
            structured = getattr(result, "structured_content", None)
            if output_schema and structured is not None and not result.is_error:
                try:
                    jsonschema.validate(structured, output_schema)
                except jsonschema.ValidationError as exc:
                    raise ToolError(f"MCP tool {tool.name} response failed schema validation: {exc.message}") from exc
            texts = [c.text[:20_000] for c in result.content if isinstance(c, TextContent)]
            return McpResult(is_error=bool(result.is_error), text=texts, structured=structured)

        self.registry.register(
            ToolSpec(
                name=name,
                description=(tool.description or "")[:500],
                permission=permission,
                handler=handler,
                input_schema=tool.input_schema,
                output_model=McpResult,
                timeout_s=cfg.timeout_s,
                source=f"mcp:{cfg.name}",
                # No built-in action type authorizes MCP writes, so an approval granted for (say) a pull
                # request can never be replayed against an MCP tool with side effects.
                approval_actions=frozenset({f"mcp_write:{name}"}),
            )
        )
        if grant and grant.agents:
            self.policy.mcp_grants[name] = set(grant.agents)
        server.tools.append(name)
