"""Tool executor: permissions, schema validation, timeouts, idempotency, MCP integration."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy import select

from orchestra.db.models import Approval, ApprovalStatus, Repository, ToolCall, Workflow
from orchestra.integrations.mcp.manager import McpConfig, McpManager, McpServerConfig, McpToolGrant
from orchestra.memory.store import MemoryStore
from orchestra.retrieval import RepositoryReader
from orchestra.schemas.agents import AgentName
from orchestra.services import Services
from orchestra.tools.builtin import ToolRuntime
from orchestra.tools.executor import ToolDenied, ToolError
from orchestra.tools.registry import Permission, ToolSpec

EXAMPLE_MCP = Path(__file__).resolve().parents[3] / "examples" / "mcp" / "docs_server.py"


async def _setup(services: Services) -> tuple[str, ToolRuntime]:
    async with services.db.session() as s:
        repo = Repository(provider="demo", full_name="acme/invoicekit")
        s.add(repo)
        await s.flush()
        wf = Workflow(title="t", request="r", mode="demo", repository_id=repo.id, created_by="operator")
        s.add(wf)
    runtime = ToolRuntime(host=services.demo_host, full_name="acme/invoicekit", default_branch="main",
                          reader=RepositoryReader(services.demo_host, "acme/invoicekit", "main"),
                          memory=services.memory, repository_id=repo.id, sandbox=None, sandbox_command="true")
    return wf.id, runtime


async def _approval(services: Services, wf_id: str, action: str, status: str = ApprovalStatus.APPROVED,
                    key: str = "k1") -> Approval:
    async with services.db.session() as s:
        ap = Approval(workflow_id=wf_id, action_type=action, title="t", risk_level="low", idempotency_key=key,
                      status=status)
        s.add(ap)
    return ap


async def _calls(services: Services) -> list[ToolCall]:
    async with services.db.session() as s:
        return list((await s.execute(select(ToolCall).order_by(ToolCall.started_at))).scalars())


async def test_read_tool_succeeds_and_is_traced(services: Services) -> None:
    wf_id, rt = await _setup(services)
    out = await services.executor.call(workflow_id=wf_id, agent=AgentName.REPOSITORY, tool="repo.search",
                                       args={"terms": ["average_item_price"]}, runtime=rt)
    assert out.value.hits[0].path == "invoicekit/totals.py"
    [call] = await _calls(services)
    assert call.status == "succeeded" and call.permission == "read" and call.duration_ms is not None


async def test_agent_cannot_use_tools_outside_its_allowlist(services: Services) -> None:
    wf_id, rt = await _setup(services)
    with pytest.raises(ToolDenied, match="not permitted"):
        await services.executor.call(workflow_id=wf_id, agent=AgentName.REPORTING, tool="repo.read_file",
                                     args={"path": "README.md"}, runtime=rt)
    with pytest.raises(ToolDenied, match="not available"):
        await services.executor.call(workflow_id=wf_id, agent=AgentName.AUTOMATION, tool="github.merge_pull",
                                     args={}, runtime=rt)
    assert [c.status for c in await _calls(services)] == ["denied", "denied"]


async def test_invalid_arguments_are_rejected_before_execution(services: Services) -> None:
    wf_id, rt = await _setup(services)
    with pytest.raises(ToolError, match="Invalid input"):
        await services.executor.call(workflow_id=wf_id, agent=AgentName.REPOSITORY, tool="repo.search",
                                     args={"terms": []}, runtime=rt)
    with pytest.raises(ToolError, match="Invalid input"):
        # branch outside the orchestra/ namespace never reaches the code host
        await services.executor.call(
            workflow_id=wf_id, agent=AgentName.AUTOMATION, tool="github.create_branch", args={"branch": "main"},
            runtime=rt, approval=await _approval(services, wf_id, "open_pull_request"), idempotency_key="x")
    assert services.demo_host.write_log == []


async def test_write_tools_require_matching_approval(services: Services) -> None:
    wf_id, rt = await _setup(services)
    args = {"branch": "orchestra/fix-1"}
    call = dict(workflow_id=wf_id, agent=AgentName.AUTOMATION, tool="github.create_branch", args=args, runtime=rt)
    with pytest.raises(ToolDenied, match="requires an approved action"):
        await services.executor.call(**call)
    pending = await _approval(services, wf_id, "open_pull_request", ApprovalStatus.PENDING, key="p")
    with pytest.raises(ToolDenied, match="not in approved state"):
        await services.executor.call(**call, approval=pending, idempotency_key="a")
    wrong_type = await _approval(services, wf_id, "create_issue", key="w")
    with pytest.raises(ToolDenied, match="does not authorize"):
        await services.executor.call(**call, approval=wrong_type, idempotency_key="b")
    async with services.db.session() as s:
        other = Workflow(title="o", request="r", mode="demo", created_by="x")
        s.add(other)
    foreign = await _approval(services, other.id, "open_pull_request", key="f")
    with pytest.raises(ToolDenied, match="different workflow"):
        await services.executor.call(**call, approval=foreign, idempotency_key="c")
    good = await _approval(services, wf_id, "open_pull_request", key="g")
    with pytest.raises(ToolDenied, match="idempotency key"):
        await services.executor.call(**call, approval=good)
    assert services.demo_host.write_log == []


async def test_write_tools_are_idempotent(services: Services) -> None:
    wf_id, rt = await _setup(services)
    approval = await _approval(services, wf_id, "create_issue")
    args = {"title": "Track work", "body": "details", "labels": []}
    first = await services.executor.call(workflow_id=wf_id, agent=AgentName.AUTOMATION, tool="github.create_issue",
                                         args=args, runtime=rt, approval=approval, idempotency_key="issue-1")
    second = await services.executor.call(workflow_id=wf_id, agent=AgentName.AUTOMATION, tool="github.create_issue",
                                          args=args, runtime=rt, approval=approval, idempotency_key="issue-1")
    assert second.reused and second.call_id == first.call_id
    assert len(services.demo_host.write_log) == 1


async def test_disabled_tools_are_blocked(services: Services) -> None:
    wf_id, rt = await _setup(services)
    services.executor.policy.disabled_tools = {"repo.tree"}
    with pytest.raises(ToolDenied, match="disabled"):
        await services.executor.call(workflow_id=wf_id, agent=AgentName.REPOSITORY, tool="repo.tree", args={},
                                     runtime=rt)


class Echo(BaseModel):
    value: int


async def test_timeouts_and_output_validation(services: Services) -> None:
    wf_id, rt = await _setup(services)

    async def slow(_: Any, __: Any) -> Echo:
        await asyncio.sleep(5)
        return Echo(value=1)

    async def wrong_shape(_: Any, __: Any) -> dict[str, Any]:
        return {"value": "not-a-number"}

    services.registry.register(ToolSpec(name="test.slow", description="", permission=Permission.READ, handler=slow,
                                        output_model=Echo, timeout_s=0.05))
    services.registry.register(ToolSpec(name="test.bad", description="", permission=Permission.READ,
                                        handler=wrong_shape, output_model=Echo))
    services.executor.policy.mcp_grants = {"test.slow": {AgentName.DEBUGGING}, "test.bad": {AgentName.DEBUGGING}}
    with pytest.raises(ToolError, match="timed out") as exc:
        await services.executor.call(workflow_id=wf_id, agent=AgentName.DEBUGGING, tool="test.slow", args={},
                                     runtime=rt)
    assert exc.value.transient
    with pytest.raises(ToolError, match="invalid response"):
        await services.executor.call(workflow_id=wf_id, agent=AgentName.DEBUGGING, tool="test.bad", args={},
                                     runtime=rt)
    assert [c.status for c in await _calls(services)] == ["timeout", "failed"]


async def test_secrets_are_redacted_from_recorded_arguments(services: Services) -> None:
    wf_id, rt = await _setup(services)
    approval = await _approval(services, wf_id, "comment_on_issue")
    await services.executor.call(workflow_id=wf_id, agent=AgentName.AUTOMATION, tool="github.comment",
                                 args={"number": 12, "body": "token ghp_abcdefghijklmnopqrstuvwxyz0123456789"},
                                 runtime=rt, approval=approval, idempotency_key="c1")
    [call] = await _calls(services)
    assert "ghp_" not in str(call.arguments) and "[REDACTED]" in call.arguments["body"]


# --- MCP ------------------------------------------------------------------------------


@pytest.fixture
async def mcp(services: Services):
    config = McpConfig(servers=[
        McpServerConfig(name="docs", command=sys.executable, args=[str(EXAMPLE_MCP)],
                        tools={"search_docs": McpToolGrant(permission=Permission.READ,
                                                           agents=[AgentName.DEBUGGING])}),
        McpServerConfig(name="broken", command="/nonexistent/binary", timeout_s=2),
    ])
    manager = McpManager(services.registry, services.executor.policy, config)
    await manager.start(connect_timeout_s=20)
    yield manager
    await manager.stop()


async def test_mcp_discovery_permissions_and_calls(services: Services, mcp: McpManager) -> None:
    wf_id, rt = await _setup(services)
    status = {s["name"]: s for s in mcp.status()}
    assert status["docs"]["connected"]
    assert set(status["docs"]["tools"]) == {"mcp.docs.search_docs", "mcp.docs.get_doc"}
    assert not status["broken"]["connected"] and "mcp:broken" in services.registry.unavailable

    search = services.registry.get("mcp.docs.search_docs")
    undeclared = services.registry.get("mcp.docs.get_doc")
    assert search.permission is Permission.READ
    assert undeclared.permission is Permission.WRITE  # default-deny for tools not declared in config

    out = await services.executor.call(workflow_id=wf_id, agent=AgentName.DEBUGGING, tool="mcp.docs.search_docs",
                                       args={"query": "empty inputs raise"}, runtime=rt)
    assert not out.value.is_error and out.value.structured["result"][0]["topic"] == "error-handling"

    with pytest.raises(ToolError, match="Invalid input"):
        await services.executor.call(workflow_id=wf_id, agent=AgentName.DEBUGGING, tool="mcp.docs.search_docs",
                                     args={"limit": 2}, runtime=rt)  # missing required "query"
    with pytest.raises(ToolDenied):
        await services.executor.call(workflow_id=wf_id, agent=AgentName.REPOSITORY, tool="mcp.docs.search_docs",
                                     args={"query": "x"}, runtime=rt)
    # an approval for a built-in action can never authorize an MCP write tool
    approval = await _approval(services, wf_id, "open_pull_request")
    services.executor.policy.mcp_grants["mcp.docs.get_doc"] = {AgentName.AUTOMATION}
    with pytest.raises(ToolDenied, match="does not authorize"):
        await services.executor.call(workflow_id=wf_id, agent=AgentName.AUTOMATION, tool="mcp.docs.get_doc",
                                     args={"topic": "money"}, runtime=rt, approval=approval, idempotency_key="m")


async def test_mcp_server_does_not_inherit_secrets(services: Services, mcp: McpManager, monkeypatch) -> None:
    server = mcp.servers["docs"]
    assert server.config.env == {}
    # _own() builds the child environment from PATH plus explicit config only
    import inspect

    from orchestra.integrations.mcp import manager

    assert '{"PATH": os.environ.get("PATH", ""), **cfg.env}' in inspect.getsource(manager.McpManager._own)


async def test_memory_store_roundtrip(services: Services) -> None:
    store: MemoryStore = services.memory
    from orchestra.memory.store import MemoryKind

    await store.remember(MemoryKind.ERROR_INVESTIGATION, "ZeroDivisionError in average_item_price for empty carts")
    await store.remember(MemoryKind.DECISION, "Use draft PRs for all agent changes")
    hits = await store.recall("empty cart division error", kinds=[MemoryKind.ERROR_INVESTIGATION])
    assert hits and hits[0].kind == "error_investigation"
    assert await store.recall("anything", kinds=[MemoryKind.OUTCOME]) == []
    assert await store.remember(MemoryKind.DECISION, "ghp_abcdefghijklmnopqrstuvwxyz0123456789") is None
    await store.remember(MemoryKind.OUTCOME, "temporary note", ttl_days=-1)
    assert await store.purge_expired() == 1
