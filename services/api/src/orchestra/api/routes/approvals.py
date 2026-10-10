from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, update

from orchestra.api.deps import get_or_404, get_services
from orchestra.db.models import Approval, ApprovalStatus, Workflow, WorkflowStatus, utcnow
from orchestra.schemas.api import ApprovalDecision, ApprovalOut, Page, RejectDecision
from orchestra.security.auth import Principal, Role, require
from orchestra.services import Services
from orchestra.workflows.events import EventType

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


@router.get("", response_model=Page[ApprovalOut])
async def list_approvals(
    services: Services = Depends(get_services),
    _: Principal = Depends(require(Role.VIEWER)),
    status: ApprovalStatus | None = None,
    workflow_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> Page[ApprovalOut]:
    stmt = select(Approval)
    if status:
        stmt = stmt.where(Approval.status == status)
    if workflow_id:
        stmt = stmt.where(Approval.workflow_id == workflow_id)
    async with services.db.session() as s:
        total = await s.scalar(select(func.count()).select_from(stmt.subquery()))
        rows = (await s.execute(stmt.order_by(Approval.created_at.desc()).limit(limit).offset(offset))).scalars().all()
    return Page(items=[ApprovalOut.model_validate(r) for r in rows], total=total or 0, limit=limit, offset=offset)


@router.get("/{approval_id}", response_model=ApprovalOut)
async def get_approval(
    approval_id: str, services: Services = Depends(get_services), _: Principal = Depends(require(Role.VIEWER))
) -> ApprovalOut:
    async with services.db.session() as s:
        return ApprovalOut.model_validate(await get_or_404(s, Approval, approval_id, "Approval"))


async def _decide(
    services: Services, approval_id: str, status: ApprovalStatus, principal: Principal, reason: str | None
) -> ApprovalOut:
    async with services.db.session() as s:
        approval = await get_or_404(s, Approval, approval_id, "Approval")
        workflow = await s.get(Workflow, approval.workflow_id)
        if approval.status == ApprovalStatus.PENDING and (
            workflow is None or workflow.status != WorkflowStatus.WAITING_FOR_APPROVAL
        ):
            raise HTTPException(409, "Workflow is not waiting for approval")
        # Compare-and-set: only one decision can move an approval out of "pending",
        # even if two approvers submit concurrently.
        result = await s.execute(
            update(Approval)
            .where(Approval.id == approval_id, Approval.status == ApprovalStatus.PENDING)
            .values(status=status, decided_by=principal.name, decided_at=utcnow(), decision_reason=reason)
        )
        if result.rowcount != 1:  # type: ignore[attr-defined]
            await s.refresh(approval)
            raise HTTPException(409, f"Approval is already {approval.status}")
        await s.refresh(approval)
        out = ApprovalOut.model_validate(approval)
    await services.events.emit(
        approval.workflow_id,
        EventType.APPROVAL_DECIDED,
        f"{status.value.capitalize()} by {principal.name}: {approval.title}",
        node="approval_gate",
        payload={"approval_id": approval_id, "status": status.value, "reason": reason},
    )
    await services.runner.on_approval_decided(approval.workflow_id)
    return out


@router.post("/{approval_id}/approve", response_model=ApprovalOut)
async def approve(
    approval_id: str,
    body: ApprovalDecision | None = None,
    services: Services = Depends(get_services),
    principal: Principal = Depends(require(Role.APPROVER)),
) -> ApprovalOut:
    return await _decide(services, approval_id, ApprovalStatus.APPROVED, principal, body.reason if body else None)


@router.post("/{approval_id}/reject", response_model=ApprovalOut)
async def reject(
    approval_id: str,
    body: RejectDecision,
    services: Services = Depends(get_services),
    principal: Principal = Depends(require(Role.APPROVER)),
) -> ApprovalOut:
    return await _decide(services, approval_id, ApprovalStatus.REJECTED, principal, body.reason)
