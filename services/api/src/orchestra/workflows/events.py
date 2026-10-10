"""Workflow event recording and live fan-out.

Every event is persisted (the audit timeline) and then published to in-process
subscribers (Server-Sent Events). Subscribers that fall behind lose live events
but can always re-read from the database by sequence number.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from enum import StrEnum
from typing import Any

from sqlalchemy import func, select

from orchestra.db.models import WorkflowEvent
from orchestra.db.session import Database
from orchestra.security.redaction import redact, redact_text


class EventType(StrEnum):
    STATUS = "status_changed"
    STAGE_STARTED = "stage_started"
    STAGE_COMPLETED = "stage_completed"
    STAGE_FAILED = "stage_failed"
    ROUTING = "routing_decision"
    AGENT_MESSAGE = "agent_message"
    AGENT_OUTPUT = "agent_output"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_DECIDED = "approval_decided"
    SECURITY = "security_warning"
    ERROR = "error"
    RETRY = "retry"


def serialize_event(e: WorkflowEvent) -> dict[str, Any]:
    return {
        "id": e.id,
        "workflow_id": e.workflow_id,
        "seq": e.seq,
        "type": e.type,
        "node": e.node,
        "agent": e.agent,
        "message": e.message,
        "payload": e.payload,
        "duration_ms": e.duration_ms,
        "simulated": e.simulated,
        "created_at": e.created_at.isoformat(),
    }


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)

    def publish(self, workflow_id: str, event: dict[str, Any]) -> None:
        for queue in list(self._subs.get(workflow_id, ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    @asynccontextmanager
    async def subscribe(self, workflow_id: str, maxsize: int = 1000) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=maxsize)
        self._subs[workflow_id].add(queue)
        try:
            yield queue
        finally:
            self._subs[workflow_id].discard(queue)
            if not self._subs[workflow_id]:
                self._subs.pop(workflow_id, None)


class EventRecorder:
    def __init__(self, db: Database, bus: EventBus) -> None:
        self.db = db
        self.bus = bus
        self._seq: dict[str, int] = {}
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def emit(
        self,
        workflow_id: str,
        type: EventType,
        message: str = "",
        *,
        node: str | None = None,
        agent: str | None = None,
        payload: dict[str, Any] | None = None,
        duration_ms: float | None = None,
        simulated: bool = False,
    ) -> dict[str, Any]:
        async with self._locks[workflow_id]:
            async with self.db.session() as s:
                if workflow_id not in self._seq:
                    current = await s.scalar(
                        select(func.max(WorkflowEvent.seq)).where(WorkflowEvent.workflow_id == workflow_id)
                    )
                    self._seq[workflow_id] = current or 0
                self._seq[workflow_id] += 1
                event = WorkflowEvent(
                    workflow_id=workflow_id,
                    seq=self._seq[workflow_id],
                    type=type.value,
                    node=node,
                    agent=agent,
                    message=redact_text(message)[:4000],
                    payload=redact(payload) if payload else None,
                    duration_ms=round(duration_ms, 2) if duration_ms is not None else None,
                    simulated=simulated,
                )
                s.add(event)
                await s.flush()
                data = serialize_event(event)
        self.bus.publish(workflow_id, data)
        return data

    def forget(self, workflow_id: str) -> None:
        self._seq.pop(workflow_id, None)
        self._locks.pop(workflow_id, None)
