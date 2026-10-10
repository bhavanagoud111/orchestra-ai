"""LangGraph orchestration graph.

    START → classify → retrieve_context → plan → dispatch ─┬─ bug ──────→ debug → review → verify ─┐
                                                          ├─ review ───→ review ────────────────┐ │
                                                          ├─ feature ──────────────────────────┐│ │
                                                          └─ analysis ─→ report → END          ││ │
                                verify ──(failed, revisions left)──→ debug                     ││ │
            propose_actions ←──────────────────────────────────────────────────────────────────┘┘─┘
            propose_actions ─(no actions)→ report
            propose_actions → approval_gate ⟂ interrupt (human) → execute_actions → report → END

Every node is wrapped to: honor cancellation, record stage events with
durations, update the workflow row, and enforce a per-node timeout. Transient
failures (rate limits, network) are retried by LangGraph's RetryPolicy with
exponential backoff; the debug↔verify loop is bounded by ``max_debug_revisions``
and the whole graph by ``recursion_limit``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy, interrupt
from sqlalchemy import select

from orchestra.agents.automation import AutomationAgent, assess_risk
from orchestra.agents.base import AgentContext, AgentError
from orchestra.agents.debugging import DebuggingAgent
from orchestra.agents.orchestrator import ROUTES, OrchestratorAgent
from orchestra.agents.planning import PlanningAgent
from orchestra.agents.reporting import ReportingAgent
from orchestra.agents.repository import RepositoryAgent
from orchestra.agents.review import CodeReviewAgent
from orchestra.agents.verification import VerificationAgent
from orchestra.db.models import Approval, ApprovalStatus, Workflow
from orchestra.db.session import Database
from orchestra.integrations.github.protocol import CodeHostError
from orchestra.memory.store import MemoryKind
from orchestra.patching import PatchResult, apply_edits, unified_diff
from orchestra.schemas.agents import (
    AgentName,
    DebugReport,
    ExecutionPlan,
    Intent,
    IntentClassification,
    ProposedAction,
    RepositoryContext,
    ReviewReport,
    VerificationReport,
)
from orchestra.tools.executor import ToolError
from orchestra.workflows.events import EventType
from orchestra.workflows.state import WorkflowState


class WorkflowCancelled(Exception):
    pass


def is_transient(exc: BaseException) -> bool:
    return bool(getattr(exc, "transient", False)) and isinstance(exc, AgentError | ToolError | CodeHostError)


NODE_AGENTS: dict[str, AgentName] = {
    "classify": AgentName.ORCHESTRATOR,
    "retrieve_context": AgentName.REPOSITORY,
    "plan": AgentName.PLANNING,
    "dispatch": AgentName.ORCHESTRATOR,
    "debug": AgentName.DEBUGGING,
    "review": AgentName.CODE_REVIEW,
    "verify": AgentName.VERIFICATION,
    "propose_actions": AgentName.AUTOMATION,
    "approval_gate": AgentName.ORCHESTRATOR,
    "execute_actions": AgentName.AUTOMATION,
    "report": AgentName.REPORTING,
}


@dataclass
class GraphDeps:
    ctx: AgentContext
    db: Database
    node_timeout_s: float
    max_debug_revisions: int
    host_simulated: bool
    require_tests_for_pr: bool = False


class Agents:
    orchestrator = OrchestratorAgent()
    repository = RepositoryAgent()
    planning = PlanningAgent()
    debugging = DebuggingAgent()
    review = CodeReviewAgent()
    verification = VerificationAgent()
    automation = AutomationAgent()
    reporting = ReportingAgent()


def _intent(state: WorkflowState) -> Intent:
    return Intent((state.get("intent") or {}).get("intent", Intent.REPOSITORY_ANALYSIS))


def _action_key(workflow_id: str, index: int, action: dict[str, Any]) -> str:
    digest = hashlib.sha256(json.dumps(action, sort_keys=True, default=str).encode()).hexdigest()[:24]
    return f"{workflow_id}:{index}:{digest}"


NodeFn = Callable[[WorkflowState], Awaitable[dict[str, Any]]]


async def _noop(state: WorkflowState) -> dict[str, Any]:
    return {}


def _node_functions(deps: GraphDeps) -> dict[str, NodeFn]:
    """Node implementations bound to one workflow's dependencies."""
    a = Agents
    ctx = deps.ctx

    def node(name: str, fn: NodeFn) -> NodeFn:
        async def wrapped(state: WorkflowState) -> dict[str, Any]:
            async with deps.db.session() as s:
                wf = await s.get(Workflow, ctx.workflow_id)
                if wf is None or wf.cancel_requested:
                    raise WorkflowCancelled()
                wf.current_node = name
                wf.step_count += 1
            ctx.node = name
            agent = NODE_AGENTS[name]
            await ctx.events.emit(
                ctx.workflow_id,
                EventType.STAGE_STARTED,
                f"{name} started",
                node=name,
                agent=agent.value,
                simulated=ctx.simulated,
            )
            started = time.perf_counter()
            try:
                update = await asyncio.wait_for(fn(state), timeout=deps.node_timeout_s)
            except TimeoutError as exc:
                await ctx.events.emit(
                    ctx.workflow_id,
                    EventType.STAGE_FAILED,
                    f"{name} exceeded {deps.node_timeout_s:.0f}s",
                    node=name,
                    agent=agent.value,
                    duration_ms=(time.perf_counter() - started) * 1000,
                )
                raise AgentError(f"Stage {name} timed out") from exc
            except (WorkflowCancelled, asyncio.CancelledError, GraphBubbleUp):
                raise
            except Exception as exc:
                transient = is_transient(exc)
                await ctx.events.emit(
                    ctx.workflow_id,
                    EventType.RETRY if transient else EventType.STAGE_FAILED,
                    f"{name} failed: {exc}" + (" (transient; retrying with backoff)" if transient else ""),
                    node=name,
                    agent=agent.value,
                    duration_ms=(time.perf_counter() - started) * 1000,
                    payload={"error": str(exc)[:1000], "transient": transient},
                )
                raise
            await ctx.events.emit(
                ctx.workflow_id,
                EventType.STAGE_COMPLETED,
                f"{name} completed",
                node=name,
                agent=agent.value,
                duration_ms=(time.perf_counter() - started) * 1000,
                simulated=ctx.simulated,
                payload={"updated": sorted(k for k in update if k != "errors")},
            )
            return update

        return wrapped

    # -- nodes ------------------------------------------------------------------------

    async def classify(state: WorkflowState) -> dict[str, Any]:
        result, refs = await a.orchestrator.classify(ctx, state["request"])
        return {"intent": result.model_dump(mode="json"), "refs": refs}

    async def retrieve_context(state: WorkflowState) -> dict[str, Any]:
        intent = IntentClassification.model_validate(state["intent"])
        result, snippets = await a.repository.investigate(
            ctx, state["request"], intent.search_terms, state.get("refs", {})
        )
        return {"context": result.model_dump(mode="json"), "snippets": snippets}

    async def plan(state: WorkflowState) -> dict[str, Any]:
        intent = IntentClassification.model_validate(state["intent"])
        repo = RepositoryContext.model_validate(state["context"])
        result = await a.planning.plan(ctx, state["request"], intent, repo, state.get("refs", {}))
        return {"plan": result.model_dump(mode="json")}

    async def dispatch(state: WorkflowState) -> dict[str, Any]:
        intent = _intent(state)
        route = ROUTES[intent]
        await ctx.events.emit(
            ctx.workflow_id,
            EventType.ROUTING,
            f"Routing {intent.value} → {' → '.join(route) or 'report'}",
            node="dispatch",
            agent=AgentName.ORCHESTRATOR.value,
            payload={"intent": intent.value, "route": route},
            simulated=ctx.simulated,
        )
        return {"route": route, "revisions": state.get("revisions", 0)}

    async def debug(state: WorkflowState) -> dict[str, Any]:
        plan_ = ExecutionPlan.model_validate(state["plan"]) if state.get("plan") else None
        feedback = (state.get("verification") or {}).get("feedback_for_debugger")
        report = await a.debugging.investigate(
            ctx, state["request"], state.get("refs", {}), state.get("snippets", []), plan_, feedback
        )
        original: dict[str, str] = {}
        changed: dict[str, str] = {}
        errors: list[str] = []
        violations: list[str] = []
        if report.proposed_fix:
            for edit in report.proposed_fix.edits:
                if edit.path not in original:
                    content = await ctx.runtime.reader.read(edit.path)
                    if content is not None:
                        original[edit.path] = content
            patch = apply_edits(original, report.proposed_fix.edits)
            changed = patch.files
            errors, violations = patch.errors, patch.policy_violations
        update: dict[str, Any] = {
            "debug": report.model_dump(mode="json"),
            "original_files": original,
            "changed_files": changed,
            "patch_errors": errors,
            "policy_violations": violations,
        }
        if feedback:
            update["revisions"] = state.get("revisions", 0) + 1
        return update

    async def review(state: WorkflowState) -> dict[str, Any]:
        intent = _intent(state)
        if intent is Intent.CODE_REVIEW:
            number = (state.get("intent") or {}).get("pull_number")
            if not number:
                report = ReviewReport(
                    verdict="comment",
                    summary="No pull request was referenced; nothing to review.",
                    findings=[],
                    test_coverage="n/a",
                )
                return {"review": report.model_dump(mode="json")}
            detail = await ctx.tool(AgentName.CODE_REVIEW, "github.get_pull_files", {"number": number})
            diffs = {f.filename: f.patch or "" for f in detail.files}
            subject = f"PR #{number}: {detail.pull.title}"
            for f in detail.files:
                await ctx.screen(AgentName.CODE_REVIEW, f.patch or "", f"PR #{number} {f.filename}")
        else:
            diffs = unified_diff(state.get("original_files", {}), state.get("changed_files", {}))
            subject = (state.get("debug") or {}).get("proposed_fix", {}).get("summary", "proposed fix")
        report = await a.review.review(ctx, subject=subject, diffs=diffs)
        return {"review": report.model_dump(mode="json")}

    async def verify(state: WorkflowState) -> dict[str, Any]:
        debug_ = DebugReport.model_validate(state["debug"]) if state.get("debug") else None
        review_ = ReviewReport.model_validate(state["review"]) if state.get("review") else None
        plan_ = ExecutionPlan.model_validate(state["plan"]) if state.get("plan") else None
        patch = PatchResult(
            files=state.get("changed_files", {}),
            errors=state.get("patch_errors", []),
            policy_violations=state.get("policy_violations", []),
        )
        report = await a.verification.verify(
            ctx, original=state.get("original_files", {}), patch=patch, debug=debug_, review=review_, plan=plan_
        )
        update: dict[str, Any] = {"verification": report.model_dump(mode="json")}
        if report.outcome == "failed" and state.get("revisions", 0) >= deps.max_debug_revisions:
            update["blocked_reason"] = "required verification checks failed"
        elif report.outcome == "unverified" and debug_ and debug_.proposed_fix and deps.require_tests_for_pr:
            update["blocked_reason"] = "tests are required before opening a pull request, but none were executed"
        return update

    async def propose_actions(state: WorkflowState) -> dict[str, Any]:
        intent = _intent(state)
        actions = a.automation.propose(
            ctx,
            intent=intent,
            request=state["request"],
            refs=state.get("refs", {}),
            original=state.get("original_files", {}),
            changed=state.get("changed_files", {}),
            plan=ExecutionPlan.model_validate(state["plan"]) if state.get("plan") else None,
            debug=DebugReport.model_validate(state["debug"]) if state.get("debug") else None,
            review=ReviewReport.model_validate(state["review"]) if state.get("review") else None,
            verification=VerificationReport.model_validate(state["verification"])
            if state.get("verification")
            else None,
            pull_number=(state.get("intent") or {}).get("pull_number"),
            blocked_reason=state.get("blocked_reason"),
        )
        await ctx.say(
            AgentName.AUTOMATION,
            f"Proposed {len(actions)} external action(s)",
            payload={"actions": [x.title for x in actions]},
        )
        return {
            "proposed_actions": [x.model_dump(mode="json") for x in actions],
            "security_findings": len(ctx.injection_findings),
        }

    async def approval_gate(state: WorkflowState) -> dict[str, Any]:
        verification = VerificationReport.model_validate(state["verification"]) if state.get("verification") else None
        review_ = ReviewReport.model_validate(state["review"]) if state.get("review") else None
        ids: list[str] = []
        created: list[Approval] = []
        async with deps.db.session() as s:
            for i, raw in enumerate(state.get("proposed_actions", [])):
                action = ProposedAction.model_validate(raw)
                key = _action_key(ctx.workflow_id, i, raw)
                existing = (await s.execute(select(Approval).where(Approval.idempotency_key == key))).scalars().first()
                if existing:
                    ids.append(existing.id)
                    continue
                level, reasons = assess_risk(
                    action, injection=ctx.injection_findings, verification=verification, review=review_
                )
                approval = Approval(
                    workflow_id=ctx.workflow_id,
                    action_type=action.action_type.value,
                    title=action.title,
                    description=action.description,
                    risk_level=level,
                    risk_reasons=reasons,
                    requested_permissions=action.requested_permissions,
                    proposed_changes=a.automation.proposed_changes(action, state.get("original_files", {})),
                    idempotency_key=key,
                    simulated=deps.host_simulated,
                )
                s.add(approval)
                await s.flush()
                ids.append(approval.id)
                created.append(approval)
        for ap in created:
            await ctx.events.emit(
                ctx.workflow_id,
                EventType.APPROVAL_REQUESTED,
                f"Approval required: {ap.title} (risk: {ap.risk_level})",
                node="approval_gate",
                agent=AgentName.ORCHESTRATOR.value,
                payload={"approval_id": ap.id, "risk_level": ap.risk_level, "action_type": ap.action_type},
            )
        decisions = interrupt({"approval_ids": ids})
        return {"approval_ids": ids, "approval_decisions": decisions}

    async def execute_actions(state: WorkflowState) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        actions = state.get("proposed_actions", [])
        for decision in state.get("approval_decisions", []):
            index = state["approval_ids"].index(decision["id"])
            action = ProposedAction.model_validate(actions[index])
            if decision["status"] != ApprovalStatus.APPROVED:
                results.append({"title": action.title, "status": "skipped", "reason": decision["status"]})
                continue
            async with deps.db.session() as s:
                approval = await s.get(Approval, decision["id"])
            if approval is None:
                results.append({"title": action.title, "status": "failed", "error": "approval record missing"})
                continue
            try:
                outcome = await a.automation.execute(ctx, approval, action)
                results.append(
                    {"title": action.title, "status": "succeeded", "simulated": deps.host_simulated, **outcome}
                )
            except (ToolError, CodeHostError) as exc:
                results.append({"title": action.title, "status": "failed", "error": str(exc)[:500]})
        return {"action_results": results}

    async def report(state: WorkflowState) -> dict[str, Any]:
        result = await a.reporting.report(ctx, dict(state))
        memory = ctx.runtime.memory
        rid = ctx.runtime.repository_id
        debug_ = state.get("debug")
        if debug_ and debug_.get("hypotheses"):
            top = debug_["hypotheses"][0]
            await memory.remember(
                MemoryKind.ERROR_INVESTIGATION,
                f"{debug_.get('error_signature') or 'Failure'}: {top['root_cause']}. "
                f"Fix: {(debug_.get('proposed_fix') or {}).get('summary', 'none proposed')}. "
                f"Verification: {(state.get('verification') or {}).get('outcome', 'n/a')}.",
                repository_id=rid,
                workflow_id=ctx.workflow_id,
            )
        verification = state.get("verification") or {}
        if verification.get("outcome") == "failed":
            await memory.remember(
                MemoryKind.FAILURE_PATTERN,
                "; ".join(verification.get("blocking_failures", [])),
                repository_id=rid,
                workflow_id=ctx.workflow_id,
            )
        for d in state.get("approval_decisions", []):
            if d["status"] == ApprovalStatus.APPROVED:
                await memory.remember(
                    MemoryKind.DECISION,
                    f"Approved: {d['title']}",
                    repository_id=rid,
                    workflow_id=ctx.workflow_id,
                    metadata={"by": d.get("decided_by")},
                )
        await memory.remember(
            MemoryKind.OUTCOME,
            f"{result.headline}. {result.summary[:400]}",
            repository_id=rid,
            workflow_id=ctx.workflow_id,
            metadata={"outcome": result.outcome},
        )
        if _intent(state) is Intent.REPOSITORY_ANALYSIS and state.get("context"):
            await a.repository.analyze_repository(
                ctx, state["request"], RepositoryContext.model_validate(state["context"])
            )
        return {"report": result.model_dump(mode="json")}

    return {
        "classify": node("classify", classify),
        "retrieve_context": node("retrieve_context", retrieve_context),
        "plan": node("plan", plan),
        "dispatch": node("dispatch", dispatch),
        "debug": node("debug", debug),
        "review": node("review", review),
        "verify": node("verify", verify),
        "propose_actions": node("propose_actions", propose_actions),
        "approval_gate": node("approval_gate", approval_gate),
        "execute_actions": node("execute_actions", execute_actions),
        "report": node("report", report),
    }


# -- routing ----------------------------------------------------------------------


def after_dispatch(state: WorkflowState) -> str:
    return {
        Intent.BUG_INVESTIGATION: "debug",
        Intent.CODE_REVIEW: "review",
        Intent.FEATURE_PLANNING: "propose_actions",
        Intent.REPOSITORY_ANALYSIS: "report",
    }[_intent(state)]


def after_debug(state: WorkflowState) -> str:
    return "review" if state.get("changed_files") else "verify"


def after_review(state: WorkflowState) -> str:
    return "verify" if _intent(state) is Intent.BUG_INVESTIGATION else "propose_actions"


def after_verify(state: WorkflowState) -> str:
    failed = (state.get("verification") or {}).get("outcome") == "failed"
    if failed and not state.get("blocked_reason"):
        return "debug"
    return "propose_actions"


def after_propose(state: WorkflowState) -> str:
    return "approval_gate" if state.get("proposed_actions") else "report"


def build_graph(deps: GraphDeps | None = None, checkpointer: Any = None) -> Any:
    """Build the compiled workflow graph. ``deps=None`` builds topology only (for visualization)."""
    fns = _node_functions(deps) if deps is not None else {name: _noop for name in NODE_AGENTS}
    retry = RetryPolicy(
        max_attempts=3, initial_interval=1.0, backoff_factor=2.0, max_interval=20.0, retry_on=is_transient
    )
    g = StateGraph(WorkflowState)
    for name, fn in fns.items():
        g.add_node(name, fn, retry_policy=retry)  # type: ignore[call-overload]
    g.add_edge(START, "classify")
    g.add_edge("classify", "retrieve_context")
    g.add_edge("retrieve_context", "plan")
    g.add_edge("plan", "dispatch")
    g.add_conditional_edges("dispatch", after_dispatch, ["debug", "review", "propose_actions", "report"])
    g.add_conditional_edges("debug", after_debug, ["review", "verify"])
    g.add_conditional_edges("review", after_review, ["verify", "propose_actions"])
    g.add_conditional_edges("verify", after_verify, ["debug", "propose_actions"])
    g.add_conditional_edges("propose_actions", after_propose, ["approval_gate", "report"])
    g.add_edge("approval_gate", "execute_actions")
    g.add_edge("execute_actions", "report")
    g.add_edge("report", END)
    return g.compile(checkpointer=checkpointer)


def graph_topology() -> dict[str, Any]:
    """Nodes and edges for the workflow builder UI."""
    graph = build_graph().get_graph()
    nodes = [
        {"id": n, "agent": NODE_AGENTS.get(n, AgentName.ORCHESTRATOR).value if n in NODE_AGENTS else None}
        for n in graph.nodes
    ]
    edges = [{"source": e.source, "target": e.target, "conditional": e.conditional} for e in graph.edges]
    return {"nodes": nodes, "edges": edges, "routes": {k.value: v for k, v in ROUTES.items()}}
