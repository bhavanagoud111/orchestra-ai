"""Background execution of workflow graphs.

Lifecycle::

    pending → running → (waiting_for_approval → running)* → completed | failed | cancelled

* Concurrency is bounded by a semaphore (``max_concurrent_workflows``).
* Each running segment has a wall-clock timeout and a LangGraph step limit.
* Waiting for approval consumes no resources: the graph is checkpointed at the
  interrupt and resumed with the recorded decisions.
* On shutdown, running workflows are left ``running`` and resumed from their
  last checkpoint at the next startup. External writes are idempotent, so a
  re-executed node cannot duplicate a branch, PR, issue, or comment.
"""

from __future__ import annotations

import asyncio
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from langgraph.errors import GraphRecursionError
from langgraph.types import Command
from sqlalchemy import select

from orchestra.agents.base import AgentContext
from orchestra.agents.reporting import deterministic_report
from orchestra.db.models import Approval, ApprovalStatus, Repository, Workflow, WorkflowStatus, utcnow
from orchestra.observability import get_logger, workflow_id_var
from orchestra.retrieval import RepositoryReader
from orchestra.tools.builtin import ToolRuntime
from orchestra.workflows.events import EventType
from orchestra.workflows.graph import GraphDeps, WorkflowCancelled, build_graph

if TYPE_CHECKING:
    from orchestra.services import Services

log = get_logger(__name__)


class RunKind(StrEnum):
    START = "start"
    RESUME_APPROVAL = "resume_approval"
    RECOVER = "recover"


class WorkflowRunner:
    def __init__(self, services: Services) -> None:
        self.s = services
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self._semaphore: asyncio.Semaphore | None = None
        self._shutting_down = False

    @property
    def semaphore(self) -> asyncio.Semaphore:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.s.store.effective().limits.max_concurrent_workflows)
        return self._semaphore

    def active(self) -> list[str]:
        return [wid for wid, t in self.tasks.items() if not t.done()]

    def submit(self, workflow_id: str, kind: RunKind = RunKind.START) -> None:
        if workflow_id in self.tasks and not self.tasks[workflow_id].done():
            return
        task = asyncio.create_task(self._execute(workflow_id, kind), name=f"workflow:{workflow_id}")
        self.tasks[workflow_id] = task

        def _forget(_task: asyncio.Task[None]) -> None:
            self.tasks.pop(workflow_id, None)

        task.add_done_callback(_forget)

    async def wait(self, workflow_id: str, seconds: float = 60.0) -> None:
        task = self.tasks.get(workflow_id)
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), seconds)

    async def on_approval_decided(self, workflow_id: str) -> None:
        async with self.s.db.session() as s:
            wf = await s.get(Workflow, workflow_id)
            pending = (
                (
                    await s.execute(
                        select(Approval).where(
                            Approval.workflow_id == workflow_id, Approval.status == ApprovalStatus.PENDING
                        )
                    )
                )
                .scalars()
                .first()
            )
        if wf is not None and wf.status == WorkflowStatus.WAITING_FOR_APPROVAL and pending is None:
            self.submit(workflow_id, RunKind.RESUME_APPROVAL)

    async def cancel(self, workflow_id: str, actor: str) -> Workflow | None:
        async with self.s.db.session() as s:
            wf = await s.get(Workflow, workflow_id)
            if wf is None or WorkflowStatus(wf.status).terminal:
                return wf
            wf.cancel_requested = True
            waiting = wf.status in (WorkflowStatus.WAITING_FOR_APPROVAL, WorkflowStatus.PENDING)
        task = self.tasks.get(workflow_id)
        if task is not None and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), 10)
            except (asyncio.CancelledError, TimeoutError):
                pass
        elif waiting:
            await self._finish_cancelled(workflow_id, actor)
        async with self.s.db.session() as s:
            return await s.get(Workflow, workflow_id)

    async def recover(self) -> None:
        async with self.s.db.session() as s:
            rows = (
                await s.execute(
                    select(Workflow.id, Workflow.status).where(
                        Workflow.status.in_([WorkflowStatus.RUNNING, WorkflowStatus.PENDING])
                    )
                )
            ).all()
        for wid, status in rows:
            log.info("recovering workflow", extra={"workflow": wid, "status": status})
            self.submit(wid, RunKind.RECOVER if status == WorkflowStatus.RUNNING else RunKind.START)

    async def shutdown(self) -> None:
        self._shutting_down = True
        tasks = [t for t in self.tasks.values() if not t.done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=10)

    # -- execution --------------------------------------------------------------------

    async def _set_status(
        self,
        workflow_id: str,
        status: WorkflowStatus,
        *,
        error: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        async with self.s.db.session() as s:
            wf = await s.get(Workflow, workflow_id)
            if wf is None:
                return
            previous = wf.status
            wf.status = status
            if status == WorkflowStatus.RUNNING and wf.started_at is None:
                wf.started_at = utcnow()
            if status.terminal:
                wf.finished_at = utcnow()
                wf.current_node = None
            if error is not None:
                wf.error = error
            if result is not None:
                wf.result = result
        if previous != status:
            await self.s.events.emit(
                workflow_id,
                EventType.STATUS,
                f"Status: {previous} → {status.value}",
                payload={"from": previous, "to": status.value, "error": error},
            )

    async def _finish_cancelled(self, workflow_id: str, actor: str) -> None:
        async with self.s.db.session() as s:
            for ap in (
                await s.execute(
                    select(Approval).where(
                        Approval.workflow_id == workflow_id, Approval.status == ApprovalStatus.PENDING
                    )
                )
            ).scalars():
                ap.status = ApprovalStatus.EXPIRED
                ap.decided_at = utcnow()
                ap.decision_reason = "workflow cancelled"
        await self._set_status(workflow_id, WorkflowStatus.CANCELLED, error=f"Cancelled by {actor}")

    async def _build(self, wf: Workflow) -> tuple[Any, AgentContext]:
        s = self.s
        async with s.db.session() as session:
            repo = await session.get(Repository, wf.repository_id) if wf.repository_id else None
        if repo is None:
            raise RuntimeError("Workflow has no connected repository")
        host = s.host_for(repo)
        reader = RepositoryReader(
            host,
            repo.full_name,
            repo.default_branch,
            max_files=s.settings.retrieval_max_files,
            max_file_bytes=s.settings.retrieval_max_file_bytes,
        )
        prefs = s.store.effective()
        s.executor.policy.disabled_tools = set(prefs.tools.disabled)
        runtime = ToolRuntime(
            host=host,
            full_name=repo.full_name,
            default_branch=repo.default_branch,
            reader=reader,
            memory=s.memory,
            repository_id=repo.id,
            sandbox=s.sandbox(),
            sandbox_command=s.settings.sandbox_command,
        )
        ctx = AgentContext(
            workflow_id=wf.id,
            mode=s.settings.mode,
            reasoner=s.reasoner(),
            executor=s.executor,
            runtime=runtime,
            events=s.events,
            settings=s.settings,
        )
        deps = GraphDeps(
            ctx=ctx,
            db=s.db,
            node_timeout_s=prefs.limits.node_timeout_s,
            max_debug_revisions=prefs.limits.max_debug_revisions,
            require_tests_for_pr=prefs.preferences.require_tests_for_pr,
            host_simulated=host.simulated,
        )
        return build_graph(deps, checkpointer=s.checkpointer), ctx

    async def _decisions(self, workflow_id: str) -> list[dict[str, Any]]:
        async with self.s.db.session() as s:
            rows = (
                (
                    await s.execute(
                        select(Approval).where(Approval.workflow_id == workflow_id).order_by(Approval.created_at)
                    )
                )
                .scalars()
                .all()
            )
        return [
            {"id": a.id, "title": a.title, "status": a.status, "decided_by": a.decided_by, "reason": a.decision_reason}
            for a in rows
        ]

    async def _execute(self, workflow_id: str, kind: RunKind) -> None:
        token = workflow_id_var.set(workflow_id)
        try:
            async with self.semaphore:
                await self._execute_inner(workflow_id, kind)
        finally:
            workflow_id_var.reset(token)

    async def _execute_inner(self, workflow_id: str, kind: RunKind) -> None:
        async with self.s.db.session() as s:
            wf = await s.get(Workflow, workflow_id)
        if wf is None or WorkflowStatus(wf.status).terminal:
            return
        limits = self.s.store.effective().limits
        config = {"configurable": {"thread_id": workflow_id}, "recursion_limit": limits.max_graph_steps}
        graph = None
        try:
            if wf.cancel_requested:
                raise WorkflowCancelled()
            graph, _ctx = await self._build(wf)
            if kind is RunKind.RESUME_APPROVAL:
                payload: Any = Command(resume=await self._decisions(workflow_id))
            elif kind is RunKind.RECOVER and (await graph.aget_state(config)).values:
                payload = None  # continue from the last checkpoint
                await self.s.events.emit(workflow_id, EventType.STATUS, "Resuming from last checkpoint after restart")
            else:
                payload = {"workflow_id": workflow_id, "request": wf.request, "errors": [], "revisions": 0}
            await self._set_status(workflow_id, WorkflowStatus.RUNNING)
            async with asyncio.timeout(limits.workflow_timeout_s):
                await graph.ainvoke(payload, config)
            snapshot = await graph.aget_state(config)
            if snapshot.next:
                await self._set_status(workflow_id, WorkflowStatus.WAITING_FOR_APPROVAL)
                async with self.s.db.session() as s:
                    w = await s.get(Workflow, workflow_id)
                    if w is not None:
                        w.intent = (snapshot.values.get("intent") or {}).get("intent")
                return
            values = snapshot.values
            report = values.get("report") or deterministic_report(values).model_dump(mode="json")
            async with self.s.db.session() as s:
                w = await s.get(Workflow, workflow_id)
                if w is not None:
                    w.intent = (values.get("intent") or {}).get("intent")
                if w is not None and w.intent == "repository_analysis" and values.get("context") and w.repository_id:
                    repo = await s.get(Repository, w.repository_id)
                    if repo is not None:
                        repo.analysis = {**values["context"], "workflow_id": workflow_id}
                        repo.last_analyzed_at = utcnow()
            if values.get("blocked_reason"):
                await self._set_status(
                    workflow_id, WorkflowStatus.FAILED, result=report, error=f"Blocked: {values['blocked_reason']}"
                )
            else:
                await self._set_status(workflow_id, WorkflowStatus.COMPLETED, result=report)
        except (WorkflowCancelled, asyncio.CancelledError):
            async with self.s.db.session() as s:
                w = await s.get(Workflow, workflow_id)
                user_cancel = bool(w and w.cancel_requested)
            if user_cancel:
                await self._finish_cancelled(workflow_id, "user")
            elif self._shutting_down:
                await self.s.events.emit(
                    workflow_id,
                    EventType.STATUS,
                    "Interrupted by service shutdown; will resume from checkpoint on restart",
                )
            else:
                raise
        except GraphRecursionError:
            await self._fail(workflow_id, graph, config, f"Exceeded the step limit ({limits.max_graph_steps})")
        except TimeoutError:
            await self._fail(workflow_id, graph, config, f"Exceeded the time limit ({limits.workflow_timeout_s:.0f}s)")
        except Exception as exc:
            log.exception("workflow failed")
            await self._fail(workflow_id, graph, config, f"{exc.__class__.__name__}: {exc}")

    async def _fail(self, workflow_id: str, graph: Any, config: dict[str, Any], error: str) -> None:
        report = None
        if graph is not None:
            try:
                values = dict((await graph.aget_state(config)).values)
                values.setdefault("errors", []).append(error)
                values["fatal_error"] = error
                report = deterministic_report(values).model_dump(mode="json")
            except Exception:  # noqa: BLE001 - best effort
                report = None
        await self.s.events.emit(workflow_id, EventType.ERROR, error)
        await self._set_status(workflow_id, WorkflowStatus.FAILED, error=error, result=report)
