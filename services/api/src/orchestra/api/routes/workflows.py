from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from orchestra.api.deps import get_or_404, get_services
from orchestra.db.models import Approval, ApprovalStatus, Repository, ToolCall, Workflow, WorkflowEvent, WorkflowStatus
from orchestra.schemas.api import (
    ApprovalOut,
    EventOut,
    Page,
    ToolCallOut,
    WorkflowCreate,
    WorkflowDetail,
    WorkflowSummary,
)
from orchestra.security.auth import Principal, Role, require
from orchestra.services import Services
from orchestra.workflows.events import serialize_event
from orchestra.workflows.graph import graph_topology

router = APIRouter(prefix="/api", tags=["workflows"])

STATE_KEYS = (
    "intent",
    "context",
    "plan",
    "route",
    "debug",
    "review",
    "verification",
    "proposed_actions",
    "approval_decisions",
    "action_results",
    "report",
    "blocked_reason",
    "changed_files",
    "revisions",
    "security_findings",
    "errors",
)


def _summary(wf: Workflow, pending: int = 0) -> WorkflowSummary:
    out = WorkflowSummary.model_validate(wf)
    if wf.started_at and wf.finished_at:
        out.duration_ms = (wf.finished_at - wf.started_at).total_seconds() * 1000
    out.pending_approvals = pending
    return out


async def _pending_counts(services: Services, ids: list[str]) -> dict[str, int]:
    if not ids:
        return {}
    async with services.db.session() as s:
        rows = await s.execute(
            select(Approval.workflow_id, func.count())
            .where(Approval.workflow_id.in_(ids), Approval.status == ApprovalStatus.PENDING)
            .group_by(Approval.workflow_id)
        )
    return dict(rows.all())


@router.post(
    "/workflows",
    status_code=202,
    response_model=WorkflowSummary,
    summary="Submit an engineering request for multi-agent execution",
)
async def create_workflow(
    body: WorkflowCreate,
    services: Services = Depends(get_services),
    principal: Principal = Depends(require(Role.OPERATOR)),
) -> WorkflowSummary:
    async with services.db.session() as s:
        if body.idempotency_key:
            existing = (
                (await s.execute(select(Workflow).where(Workflow.idempotency_key == body.idempotency_key)))
                .scalars()
                .first()
            )
            if existing:
                return _summary(existing)
        await get_or_404(s, Repository, body.repository_id, "Repository")
        title = body.title or body.request.strip().splitlines()[0][:120]
        wf = Workflow(
            title=title,
            request=body.request,
            mode=services.settings.mode.value,
            repository_id=body.repository_id,
            created_by=principal.name,
            idempotency_key=body.idempotency_key,
        )
        s.add(wf)
    services.runner.submit(wf.id)
    return _summary(wf)


@router.get("/workflows", response_model=Page[WorkflowSummary])
async def list_workflows(
    services: Services = Depends(get_services),
    _: Principal = Depends(require(Role.VIEWER)),
    status: WorkflowStatus | None = None,
    repository_id: str | None = None,
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> Page[WorkflowSummary]:
    stmt = select(Workflow)
    if status:
        stmt = stmt.where(Workflow.status == status)
    if repository_id:
        stmt = stmt.where(Workflow.repository_id == repository_id)
    async with services.db.session() as s:
        total = await s.scalar(select(func.count()).select_from(stmt.subquery()))
        rows = (await s.execute(stmt.order_by(Workflow.created_at.desc()).limit(limit).offset(offset))).scalars().all()
    pending = await _pending_counts(services, [w.id for w in rows])
    return Page(items=[_summary(w, pending.get(w.id, 0)) for w in rows], total=total or 0, limit=limit, offset=offset)


@router.get("/workflows/{workflow_id}", response_model=WorkflowDetail)
async def get_workflow(
    workflow_id: str, services: Services = Depends(get_services), _: Principal = Depends(require(Role.VIEWER))
) -> WorkflowDetail:
    async with services.db.session() as s:
        wf = await get_or_404(s, Workflow, workflow_id, "Workflow")
        approvals = (
            (await s.execute(select(Approval).where(Approval.workflow_id == workflow_id).order_by(Approval.created_at)))
            .scalars()
            .all()
        )
    state: dict[str, Any] = {}
    if services.checkpointer is not None:
        checkpoint = await services.checkpointer.aget({"configurable": {"thread_id": workflow_id}})
        if checkpoint:
            values = checkpoint.get("channel_values", {})
            state = {k: values[k] for k in STATE_KEYS if k in values}
    pending = sum(a.status == ApprovalStatus.PENDING for a in approvals)
    base = _summary(wf, pending).model_dump()
    return WorkflowDetail(
        **base,
        request=wf.request,
        result=wf.result,
        state=state,
        approvals=[ApprovalOut.model_validate(a) for a in approvals],
    )


@router.post("/workflows/{workflow_id}/cancel", response_model=WorkflowSummary)
async def cancel_workflow(
    workflow_id: str, services: Services = Depends(get_services), principal: Principal = Depends(require(Role.OPERATOR))
) -> WorkflowSummary:
    async with services.db.session() as s:
        wf = await get_or_404(s, Workflow, workflow_id, "Workflow")
    if WorkflowStatus(wf.status).terminal:
        raise HTTPException(409, f"Workflow is already {wf.status}")
    cancelled = await services.runner.cancel(workflow_id, principal.name)
    return _summary(cancelled or wf)


@router.get(
    "/workflows/{workflow_id}/events",
    response_model=list[EventOut],
    summary="Workflow timeline. Send `Accept: text/event-stream` (or ?stream=true) for live SSE.",
)
async def workflow_events(
    workflow_id: str,
    request: Request,
    services: Services = Depends(get_services),
    _: Principal = Depends(require(Role.VIEWER)),
    after: int = Query(0, ge=0),
    stream: bool = False,
    limit: int = Query(500, ge=1, le=2000),
) -> Any:
    async with services.db.session() as s:
        await get_or_404(s, Workflow, workflow_id, "Workflow")
    last_event_id = request.headers.get("last-event-id")
    if last_event_id and last_event_id.isdigit():
        after = max(after, int(last_event_id))
    if stream or "text/event-stream" in request.headers.get("accept", ""):
        return StreamingResponse(
            _sse(services, request, workflow_id, after),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    async with services.db.session() as s:
        rows = (
            (
                await s.execute(
                    select(WorkflowEvent)
                    .where(WorkflowEvent.workflow_id == workflow_id, WorkflowEvent.seq > after)
                    .order_by(WorkflowEvent.seq)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
    return [serialize_event(e) for e in rows]


def _format(event: dict[str, Any]) -> str:
    return f"id: {event['seq']}\nevent: workflow_event\ndata: {json.dumps(event, default=str)}\n\n"


async def _sse(services: Services, request: Request, workflow_id: str, after: int) -> AsyncIterator[str]:
    last = after
    async with services.bus.subscribe(workflow_id) as queue:
        async with services.db.session() as s:
            rows = (
                (
                    await s.execute(
                        select(WorkflowEvent)
                        .where(WorkflowEvent.workflow_id == workflow_id, WorkflowEvent.seq > last)
                        .order_by(WorkflowEvent.seq)
                    )
                )
                .scalars()
                .all()
            )
            wf = await s.get(Workflow, workflow_id)
        for e in rows:
            last = e.seq
            yield _format(serialize_event(e))
        if wf is not None and WorkflowStatus(wf.status).terminal and queue.empty():
            yield f"event: end\ndata: {json.dumps({'status': wf.status})}\n\n"
            return
        yield "retry: 3000\n\n"
        while not await request.is_disconnected():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15)
            except TimeoutError:
                async with services.db.session() as s:
                    wf = await s.get(Workflow, workflow_id)
                if wf is None or WorkflowStatus(wf.status).terminal:
                    yield f"event: end\ndata: {json.dumps({'status': wf.status if wf else 'missing'})}\n\n"
                    return
                yield ": keepalive\n\n"
                continue
            if event["seq"] <= last:
                continue
            last = event["seq"]
            yield _format(event)
            if event["type"] == "status_changed" and (event.get("payload") or {}).get("to") in {
                "completed",
                "failed",
                "cancelled",
            }:
                yield f"event: end\ndata: {json.dumps({'status': event['payload']['to']})}\n\n"
                return


@router.get("/workflows/{workflow_id}/tool-calls", response_model=list[ToolCallOut])
async def workflow_tool_calls(
    workflow_id: str, services: Services = Depends(get_services), _: Principal = Depends(require(Role.VIEWER))
) -> list[ToolCallOut]:
    async with services.db.session() as s:
        rows = (
            (await s.execute(select(ToolCall).where(ToolCall.workflow_id == workflow_id).order_by(ToolCall.started_at)))
            .scalars()
            .all()
        )
    return [ToolCallOut.model_validate(r) for r in rows]


@router.get("/workflow-graph", summary="Workflow graph topology (nodes, edges, intent routes)")
async def workflow_graph(_: Principal = Depends(require(Role.VIEWER))) -> dict[str, Any]:
    return graph_topology()
