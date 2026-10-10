"""Tool execution with permission checks, validation, timeouts, tracing, and idempotency."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select

from orchestra.db.models import Approval, ApprovalStatus, ToolCall, ToolCallStatus
from orchestra.db.session import Database
from orchestra.observability import get_logger
from orchestra.schemas.agents import AgentName
from orchestra.security.injection import scan
from orchestra.security.redaction import redact, redact_text
from orchestra.tools.policy import ToolPolicy
from orchestra.tools.registry import Permission, ToolRegistry, ToolSpec, ToolValidationError
from orchestra.workflows.events import EventRecorder, EventType

log = get_logger(__name__)


class ToolError(RuntimeError):
    def __init__(self, message: str, *, status: ToolCallStatus = ToolCallStatus.FAILED, transient: bool = False):
        super().__init__(message)
        self.status = status
        self.transient = transient


class ToolDenied(ToolError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status=ToolCallStatus.DENIED)


@dataclass
class ToolOutcome:
    call_id: str
    tool: str
    value: Any
    reused: bool = False


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    return value


def _summarize(value: Any, limit: int = 300) -> str:
    text = value if isinstance(value, str) else json.dumps(_jsonable(value), default=str)
    return redact_text(text[:limit] + ("…" if len(text) > limit else ""))


class ToolExecutor:
    def __init__(
        self, db: Database, registry: ToolRegistry, events: EventRecorder, *, default_timeout_s: float = 30.0
    ) -> None:
        self.db = db
        self.registry = registry
        self.events = events
        self.default_timeout_s = default_timeout_s
        self.policy = ToolPolicy()

    async def call(
        self,
        *,
        workflow_id: str | None,
        agent: AgentName,
        tool: str,
        args: dict[str, Any],
        runtime: Any,
        approval: Approval | None = None,
        idempotency_key: str | None = None,
        simulated: bool = False,
        node: str | None = None,
    ) -> ToolOutcome:
        spec = self.registry.get(tool)
        if spec is None:
            await self._record_denied(workflow_id, agent, tool, args, "unknown or unavailable tool", node)
            raise ToolDenied(f"Tool {tool} is not available")

        ok, reason = self.policy.allowed(agent, tool)
        if not ok:
            await self._record_denied(workflow_id, agent, tool, args, reason, node, spec)
            raise ToolDenied(reason)

        if spec.permission is Permission.WRITE:
            await self._check_write_authorization(spec, workflow_id, approval, idempotency_key)
            assert idempotency_key is not None
            prior = await self._prior_success(idempotency_key)
            if prior is not None:
                await self._emit(
                    workflow_id,
                    EventType.TOOL_RESULT,
                    f"{tool}: reused result of earlier call",
                    node=node,
                    agent=agent,
                    payload={"tool": tool, "call_id": prior.id, "reused": True},
                    simulated=simulated,
                )
                return ToolOutcome(prior.id, tool, prior.result, reused=True)

        try:
            parsed = spec.validate_input(args)
        except ToolValidationError as exc:
            await self._record_denied(
                workflow_id, agent, tool, args, str(exc), node, spec, status=ToolCallStatus.FAILED
            )
            raise ToolError(str(exc)) from exc

        call = ToolCall(
            workflow_id=workflow_id,
            agent=agent.value,
            tool=tool,
            source=spec.source,
            permission=spec.permission.value,
            arguments=redact(args),
            status=ToolCallStatus.RUNNING,
            idempotency_key=idempotency_key,
            approval_id=approval.id if approval else None,
            simulated=simulated,
        )
        async with self.db.session() as s:
            s.add(call)
        await self._emit(
            workflow_id,
            EventType.TOOL_CALL,
            f"{agent.value} → {tool}",
            node=node,
            agent=agent,
            payload={
                "tool": tool,
                "call_id": call.id,
                "permission": spec.permission.value,
                "source": spec.source,
                "arguments": redact(args),
            },
            simulated=simulated,
        )

        timeout = spec.timeout_s or self.default_timeout_s
        started = time.perf_counter()
        status = ToolCallStatus.SUCCEEDED
        error: str | None = None
        value: Any = None
        try:
            raw = await asyncio.wait_for(spec.handler(parsed, runtime), timeout=timeout)
            value = spec.validate_output(raw)
        except TimeoutError:
            status, error = ToolCallStatus.TIMEOUT, f"{tool} timed out after {timeout:.0f}s"
        except ToolValidationError as exc:
            status, error = ToolCallStatus.FAILED, str(exc)
        except ToolError as exc:
            status, error = exc.status, str(exc)
        except asyncio.CancelledError:
            status, error = ToolCallStatus.FAILED, "cancelled"
            await self._finish(call.id, status, error, None, started)
            raise
        except Exception as exc:
            transient = bool(getattr(exc, "transient", False))
            status, error = ToolCallStatus.FAILED, f"{exc.__class__.__name__}: {exc}"
            await self._finish(call.id, status, error, None, started)
            await self._emit(
                workflow_id,
                EventType.TOOL_RESULT,
                f"{tool} failed: {redact_text(error)}",
                node=node,
                agent=agent,
                payload={"tool": tool, "call_id": call.id, "status": status},
                duration_ms=(time.perf_counter() - started) * 1000,
                simulated=simulated,
            )
            raise ToolError(error, transient=transient) from exc

        result_json = _jsonable(value) if status is ToolCallStatus.SUCCEEDED else None
        duration = await self._finish(call.id, status, error, result_json, started)
        if status is not ToolCallStatus.SUCCEEDED:
            await self._emit(
                workflow_id,
                EventType.TOOL_RESULT,
                f"{tool} {status.value}: {error}",
                node=node,
                agent=agent,
                payload={"tool": tool, "call_id": call.id, "status": status.value},
                duration_ms=duration,
                simulated=simulated,
            )
            raise ToolError(error or status.value, status=status, transient=status is ToolCallStatus.TIMEOUT)

        await self._emit(
            workflow_id,
            EventType.TOOL_RESULT,
            f"{tool} succeeded",
            node=node,
            agent=agent,
            payload={"tool": tool, "call_id": call.id, "status": status.value, "summary": _summarize(result_json)},
            duration_ms=duration,
            simulated=simulated,
        )
        if spec.source != "builtin":
            await self._screen_external(workflow_id, tool, result_json, node, agent)
        return ToolOutcome(call.id, tool, value)

    async def _check_write_authorization(
        self, spec: ToolSpec, workflow_id: str | None, approval: Approval | None, idempotency_key: str | None
    ) -> None:
        if approval is None:
            raise ToolDenied(f"{spec.name} is a write operation and requires an approved action")
        async with self.db.session() as s:
            current = await s.get(Approval, approval.id)
        if current is None or current.status != ApprovalStatus.APPROVED:
            raise ToolDenied(f"Approval {approval.id} is not in approved state")
        if current.workflow_id != workflow_id:
            raise ToolDenied("Approval belongs to a different workflow")
        if spec.approval_actions and current.action_type not in spec.approval_actions:
            raise ToolDenied(f"Approval for {current.action_type} does not authorize {spec.name}")
        if not idempotency_key:
            raise ToolDenied("Write operations require an idempotency key")

    async def _prior_success(self, key: str) -> ToolCall | None:
        async with self.db.session() as s:
            return (
                (
                    await s.execute(
                        select(ToolCall).where(
                            ToolCall.idempotency_key == key, ToolCall.status == ToolCallStatus.SUCCEEDED
                        )
                    )
                )
                .scalars()
                .first()
            )

    async def _finish(
        self, call_id: str, status: ToolCallStatus, error: str | None, result: Any, started: float
    ) -> float:
        duration = (time.perf_counter() - started) * 1000
        async with self.db.session() as s:
            row = await s.get(ToolCall, call_id)
            if row is not None:
                row.status = status
                row.error = redact_text(error) if error else None
                if result is None:
                    row.result = None
                elif isinstance(result, dict):
                    row.result = redact(result)
                elif isinstance(result, list):
                    row.result = {"items": redact(result)}
                else:
                    row.result = {"value": redact(result)}
                row.result_summary = _summarize(result) if result is not None else None
                row.duration_ms = round(duration, 2)
        return duration

    async def _record_denied(
        self,
        workflow_id: str | None,
        agent: AgentName,
        tool: str,
        args: dict[str, Any],
        reason: str,
        node: str | None,
        spec: ToolSpec | None = None,
        status: ToolCallStatus = ToolCallStatus.DENIED,
    ) -> None:
        async with self.db.session() as s:
            s.add(
                ToolCall(
                    workflow_id=workflow_id,
                    agent=agent.value,
                    tool=tool,
                    source=spec.source if spec else "unknown",
                    permission=spec.permission.value if spec else "unknown",
                    arguments=redact(args),
                    status=status,
                    error=reason,
                    duration_ms=0,
                )
            )
        await self._emit(
            workflow_id,
            EventType.SECURITY if status is ToolCallStatus.DENIED else EventType.ERROR,
            f"Tool call {tool} by {agent.value} {status.value}: {reason}",
            node=node,
            agent=agent,
            payload={"tool": tool, "reason": reason},
        )
        log.warning("tool call rejected", extra={"tool": tool, "tool_agent": agent.value, "reason": reason})

    async def _screen_external(
        self, workflow_id: str | None, tool: str, result: Any, node: str | None, agent: AgentName
    ) -> None:
        findings = scan(json.dumps(result, default=str)[:50_000], source=f"tool:{tool}")
        if findings:
            await self._emit(
                workflow_id,
                EventType.SECURITY,
                f"Possible prompt injection in {tool} response",
                node=node,
                agent=agent,
                payload={"findings": [f.__dict__ for f in findings]},
            )

    async def _emit(self, workflow_id: str | None, type: EventType, message: str, **kw: Any) -> None:
        if workflow_id is None:
            return
        agent = kw.pop("agent", None)
        await self.events.emit(workflow_id, type, message, agent=agent.value if agent else None, **kw)
