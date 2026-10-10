from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text

from orchestra import __version__
from orchestra.api.deps import get_services
from orchestra.db.models import Approval, ApprovalStatus, Repository, ToolCall, Workflow, WorkflowEvent, utcnow
from orchestra.integrations.github.protocol import CodeHostError, RepoInfo
from orchestra.schemas.agents import AgentName
from orchestra.schemas.api import AgentOut, Health, Me, Metrics, ToolOut, ToolsResponse
from orchestra.security.auth import Principal, Role, require
from orchestra.services import Services
from orchestra.settings_store import EffectiveSettings, SettingsUpdate
from orchestra.tools.policy import AGENT_TOOLS
from orchestra.workflows.graph import NODE_AGENTS, Agents

router = APIRouter(prefix="/api", tags=["system"])

AGENT_CLASSES = [
    Agents.orchestrator,
    Agents.repository,
    Agents.planning,
    Agents.debugging,
    Agents.review,
    Agents.automation,
    Agents.verification,
    Agents.reporting,
]


@router.get("/health", response_model=Health)
async def health(services: Services = Depends(get_services)) -> Health:
    checks: dict[str, str] = {}
    try:
        async with services.db.session() as s:
            await s.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"error: {exc.__class__.__name__}"
    checks["checkpointer"] = "ok" if services.checkpointer is not None else "unavailable"
    checks["llm"] = "demo" if services.settings.is_demo else ("configured" if services.reasoner() else "missing key")
    checks["github"] = "connected" if services.store.secret("github_token") else "not connected"
    for server in services.mcp.status():
        checks[f"mcp:{server['name']}"] = "ok" if server["connected"] else f"error: {server['error']}"
    degraded = checks["database"] != "ok" or checks["checkpointer"] != "ok" or checks["llm"] == "missing key"
    return Health(
        status="degraded" if degraded else "ok", version=__version__, mode=services.settings.mode.value, checks=checks
    )


@router.get("/me", response_model=Me)
async def me(services: Services = Depends(get_services), principal: Principal = Depends(require(Role.VIEWER))) -> Me:
    return Me(name=principal.name, role=principal.role.name.lower(), mode=services.settings.mode.value)


@router.get("/metrics", response_model=Metrics)
async def metrics(services: Services = Depends(get_services), _: Principal = Depends(require(Role.VIEWER))) -> Metrics:
    since = utcnow() - timedelta(days=7)
    async with services.db.session() as s:
        by_status = dict((await s.execute(select(Workflow.status, func.count()).group_by(Workflow.status))).all())
        by_intent = dict(
            (
                await s.execute(
                    select(Workflow.intent, func.count()).where(Workflow.intent.is_not(None)).group_by(Workflow.intent)
                )
            ).all()
        )
        approvals = dict((await s.execute(select(Approval.status, func.count()).group_by(Approval.status))).all())
        tools = dict((await s.execute(select(ToolCall.status, func.count()).group_by(ToolCall.status))).all())
        repos = await s.scalar(select(func.count()).select_from(Repository))
        finished = (
            await s.execute(
                select(Workflow.started_at, Workflow.finished_at)
                .where(Workflow.started_at.is_not(None), Workflow.finished_at.is_not(None))
                .limit(500)
            )
        ).all()
        recent = (
            await s.execute(select(Workflow.created_at, Workflow.status).where(Workflow.created_at >= since))
        ).all()
    durations = [(f - st).total_seconds() * 1000 for st, f in finished if st and f]
    days: dict[str, dict[str, int]] = {}
    for i in range(7):
        day = (utcnow() - timedelta(days=6 - i)).date().isoformat()
        days[day] = {"completed": 0, "failed": 0, "other": 0}
    for created, status in recent:
        bucket = days.get(created.date().isoformat())
        if bucket is not None:
            bucket[status if status in ("completed", "failed") else "other"] += 1
    return Metrics(
        workflows_by_status=by_status,
        workflows_by_intent={k: v for k, v in by_intent.items() if k},
        active_workflows=by_status.get("running", 0) + by_status.get("pending", 0),
        pending_approvals=approvals.get(ApprovalStatus.PENDING, 0),
        connected_repositories=repos or 0,
        avg_duration_ms=sum(durations) / len(durations) if durations else None,
        tool_calls=tools,
        approvals_by_status=approvals,
        executions_last_7d=[{"date": d, **v} for d, v in days.items()],
    )


@router.get("/agents", response_model=list[AgentOut])
async def agents(
    services: Services = Depends(get_services), _: Principal = Depends(require(Role.VIEWER))
) -> list[AgentOut]:
    since = utcnow() - timedelta(hours=24)
    async with services.db.session() as s:
        running = (await s.execute(select(Workflow).where(Workflow.status == "running"))).scalars().all()
        calls = dict(
            (
                await s.execute(
                    select(ToolCall.agent, func.count()).where(ToolCall.started_at >= since).group_by(ToolCall.agent)
                )
            ).all()
        )
        failures = dict(
            (
                await s.execute(
                    select(ToolCall.agent, func.count())
                    .where(ToolCall.started_at >= since, ToolCall.status != "succeeded")
                    .group_by(ToolCall.agent)
                )
            ).all()
        )
        out: list[AgentOut] = []
        for agent in AGENT_CLASSES:
            name = agent.name.value
            current = next(
                (w for w in running if w.current_node and NODE_AGENTS.get(w.current_node) == agent.name), None
            )
            last = (
                (
                    await s.execute(
                        select(WorkflowEvent)
                        .where(WorkflowEvent.agent == name, WorkflowEvent.type == "stage_completed")
                        .order_by(WorkflowEvent.created_at.desc())
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            recent = (
                (
                    await s.execute(
                        select(ToolCall).where(ToolCall.agent == name).order_by(ToolCall.started_at.desc()).limit(5)
                    )
                )
                .scalars()
                .all()
            )
            out.append(
                AgentOut(
                    **agent.describe(),
                    status="active" if current else "idle",
                    current_task={"workflow_id": current.id, "title": current.title, "node": current.current_node}
                    if current
                    else None,
                    last_execution={
                        "workflow_id": last.workflow_id,
                        "node": last.node,
                        "at": last.created_at.isoformat(),
                        "duration_ms": last.duration_ms,
                        "simulated": last.simulated,
                    }
                    if last
                    else None,
                    tool_calls_24h=calls.get(name, 0),
                    tool_failures_24h=failures.get(name, 0),
                    recent_tools=[
                        {
                            "tool": c.tool,
                            "status": c.status,
                            "at": c.started_at.isoformat(),
                            "workflow_id": c.workflow_id,
                            "simulated": c.simulated,
                        }
                        for c in recent
                    ],
                )
            )
    return out


@router.get("/tools", response_model=ToolsResponse)
async def tools(
    services: Services = Depends(get_services), _: Principal = Depends(require(Role.VIEWER))
) -> ToolsResponse:
    disabled = set(services.store.effective().tools.disabled)
    policy = services.executor.policy
    items = []
    for spec in services.registry.all():
        d = spec.describe()
        allowed = sorted(
            {a.value for a, names in AGENT_TOOLS.items() if spec.name in names}
            | {a.value for a in policy.mcp_grants.get(spec.name, set())}
        )
        items.append(ToolOut(**d, enabled=spec.name not in disabled, allowed_agents=allowed))
    return ToolsResponse(tools=items, unavailable=services.registry.unavailable, mcp_servers=services.mcp.status())


class SettingsOut(BaseModel):
    effective: EffectiveSettings
    secrets: dict[str, dict[str, Any]]
    agents: list[str] = Field(default_factory=lambda: [a.value for a in AgentName])
    secret_storage_available: bool


@router.get("/settings", response_model=SettingsOut)
async def get_settings_route(
    services: Services = Depends(get_services), _: Principal = Depends(require(Role.VIEWER))
) -> SettingsOut:
    return SettingsOut(
        effective=services.store.effective(),
        secrets=services.store.secret_status(),
        secret_storage_available=services.store.box.available,
    )


@router.put("/settings", response_model=SettingsOut)
async def update_settings(
    body: SettingsUpdate,
    services: Services = Depends(get_services),
    principal: Principal = Depends(require(Role.ADMIN)),
) -> SettingsOut:
    if body.secrets and not services.store.box.available:
        raise HTTPException(409, "Set ORCHESTRA_SECRET_KEY to store secrets from the UI")
    if body.tools:
        unknown = set(body.tools.disabled) - {t.name for t in services.registry.all()}
        if unknown:
            raise HTTPException(422, f"Unknown tools: {sorted(unknown)}")
    await services.store.update(body, principal.name)
    return await get_settings_route(services, principal)


# --- GitHub connection --------------------------------------------------------------


@router.get("/github/status", tags=["github"])
async def github_status(
    services: Services = Depends(get_services), _: Principal = Depends(require(Role.VIEWER))
) -> dict[str, Any]:
    client = services.github()
    if client is None:
        return {"connected": False, "device_flow_available": bool(services.settings.github_oauth_client_id)}
    try:
        user = await client.get_user()
    except CodeHostError as exc:
        return {
            "connected": False,
            "error": str(exc)[:200],
            "device_flow_available": bool(services.settings.github_oauth_client_id),
        }
    return {"connected": True, "user": user, "device_flow_available": bool(services.settings.github_oauth_client_id)}


@router.get("/github/repos", response_model=list[RepoInfo], tags=["github"])
async def github_repos(
    page: int = 1, services: Services = Depends(get_services), _: Principal = Depends(require(Role.OPERATOR))
) -> list[RepoInfo]:
    client = services.github()
    if client is None:
        raise HTTPException(409, "GitHub is not connected")
    try:
        return await client.list_repos(page=page, per_page=50)
    except CodeHostError as exc:
        raise HTTPException(502, str(exc)) from exc


class DevicePoll(BaseModel):
    device_code: str = Field(min_length=10, max_length=200)


@router.post("/github/device/start", tags=["github"])
async def github_device_start(
    services: Services = Depends(get_services), _: Principal = Depends(require(Role.ADMIN))
) -> dict[str, Any]:
    """Start the GitHub OAuth device flow. The browser shows the user code; the token never reaches it."""
    client_id = services.settings.github_oauth_client_id
    if not client_id:
        raise HTTPException(409, "ORCHESTRA_GITHUB_OAUTH_CLIENT_ID is not configured")
    async with httpx.AsyncClient(timeout=15) as http:
        resp = await http.post(
            "https://github.com/login/device/code",
            headers={"Accept": "application/json"},
            data={"client_id": client_id, "scope": "repo"},
        )
    data = resp.json()
    if resp.status_code != 200 or "device_code" not in data:
        raise HTTPException(502, f"GitHub device flow failed: {data.get('error_description', resp.status_code)}")
    return {k: data[k] for k in ("device_code", "user_code", "verification_uri", "expires_in", "interval")}


@router.post("/github/device/poll", tags=["github"])
async def github_device_poll(
    body: DevicePoll, services: Services = Depends(get_services), principal: Principal = Depends(require(Role.ADMIN))
) -> dict[str, Any]:
    client_id = services.settings.github_oauth_client_id
    if not client_id:
        raise HTTPException(409, "ORCHESTRA_GITHUB_OAUTH_CLIENT_ID is not configured")
    if not services.store.box.available:
        raise HTTPException(409, "Set ORCHESTRA_SECRET_KEY so the GitHub token can be stored encrypted")
    async with httpx.AsyncClient(timeout=15) as http:
        resp = await http.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": client_id,
                "device_code": body.device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
        )
    data = resp.json()
    if "access_token" in data:
        await services.store.update(SettingsUpdate(secrets={"github_token": data["access_token"]}), principal.name)
        return {"status": "connected"}
    return {"status": data.get("error", "pending")}
