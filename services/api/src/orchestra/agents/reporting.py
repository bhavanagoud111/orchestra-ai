"""Reporting agent: execution summaries and audit-friendly explanations."""

from __future__ import annotations

from typing import Any

from orchestra.agents.base import Agent, AgentContext, AgentError
from orchestra.schemas.agents import AgentDecision, AgentName, FinalReport, ReportOutcome
from orchestra.workflows.events import EventType


def deterministic_report(state: dict[str, Any]) -> FinalReport:
    intent = (state.get("intent") or {}).get("intent", "unknown")
    decisions: list[AgentDecision] = []
    completed: list[str] = []
    pending: list[str] = []
    attention: list[str] = []

    if state.get("intent"):
        decisions.append(
            AgentDecision(
                agent=AgentName.ORCHESTRATOR,
                decision=f"Routed as {intent}",
                rationale=state["intent"].get("rationale", ""),
            )
        )
    if ctx := state.get("context"):
        completed.append(f"Ranked {len(ctx.get('relevant_files', []))} relevant files")
    if plan := state.get("plan"):
        completed.append(f"Planned {len(plan.get('tasks', []))} tasks")
    if debug := state.get("debug"):
        top = (debug.get("hypotheses") or [{}])[0]
        decisions.append(
            AgentDecision(
                agent=AgentName.DEBUGGING,
                decision=top.get("root_cause", "No hypothesis"),
                rationale="; ".join(top.get("evidence", [])[:2]),
            )
        )
        if debug.get("proposed_fix"):
            completed.append(f"Proposed fix: {debug['proposed_fix']['summary']}")
        for need in debug.get("needs_more_evidence", []):
            pending.append(f"Evidence needed: {need}")
    if review := state.get("review"):
        decisions.append(
            AgentDecision(
                agent=AgentName.CODE_REVIEW,
                decision=f"Verdict: {review['verdict']}",
                rationale=review.get("summary", ""),
            )
        )
        completed.append(f"Code review with {len(review.get('findings', []))} findings")
    if ver := state.get("verification"):
        decisions.append(
            AgentDecision(
                agent=AgentName.VERIFICATION,
                decision=f"Outcome: {ver['outcome']}",
                rationale="; ".join(ver.get("blocking_failures", [])) or "No blocking failures",
            )
        )
        if ver["outcome"] == "failed":
            attention += [f"Blocking check: {f}" for f in ver.get("blocking_failures", [])]
        elif ver["outcome"] == "unverified" and (state.get("debug") or {}).get("proposed_fix"):
            attention.append("Tests were not executed; run the test suite before merging")

    for d in state.get("approval_decisions", []):
        line = f"{d['title']}: {d['status']} by {d.get('decided_by') or 'unknown'}"
        (completed if d["status"] == "approved" else pending).append(line)
    for r in state.get("action_results", []):
        if r.get("error"):
            attention.append(f"Action failed: {r['title']}: {r['error']}")
        else:
            completed.append(
                f"{r['title']} → {r.get('url') or 'done'}" + (" (simulated)" if r.get("simulated") else "")
            )
    if state.get("security_findings"):
        attention.append(
            f"{state['security_findings']} possible prompt-injection attempt(s) were detected in "
            "repository or issue content and ignored"
        )
    for err in state.get("errors", []):
        attention.append(f"Error: {err}")

    blocked = bool(state.get("blocked_reason"))
    failed = bool(state.get("fatal_error")) or (bool(state.get("errors")) and not completed)
    outcome: ReportOutcome = (
        "failed" if failed else "blocked" if blocked else "partial" if pending or attention else "completed"
    )
    headline = {
        "completed": f"{intent.replace('_', ' ').capitalize()} completed",
        "partial": f"{intent.replace('_', ' ').capitalize()} completed with items needing attention",
        "blocked": f"{intent.replace('_', ' ').capitalize()} blocked: {state.get('blocked_reason')}",
        "failed": f"{intent.replace('_', ' ').capitalize()} failed",
    }[outcome]
    summary = (state.get("context") or {}).get("summary", "")
    return FinalReport(
        headline=headline,
        outcome=outcome,
        summary=summary[:600],
        completed=completed,
        pending=pending,
        decisions=decisions,
        attention_required=attention,
    )


class ReportingAgent(Agent):
    name = AgentName.REPORTING
    title = "Reporting"
    responsibility = (
        "Generates execution summaries, explains agent decisions, lists completed and pending work, and "
        "highlights actions that need human attention."
    )
    instructions = (
        "Write the final execution report for an engineering audience. The deterministic report is the source "
        "of truth for what happened: keep its outcome, completed, pending and attention_required facts, and "
        "improve the headline, summary and decision rationales for clarity. Do not claim anything that is not "
        "supported by the workflow state."
    )

    async def report(self, ctx: AgentContext, state: dict[str, Any]) -> FinalReport:
        base = deterministic_report(state)
        context = (
            ctx.context()
            .add("Deterministic report", base.model_dump_json(), priority=100)
            .add(
                "Workflow state",
                str({k: v for k, v in state.items() if k not in {"snippets", "original_files"}})[:20000],
                priority=60,
            )
        )
        try:
            report = await self.reason(ctx, context=context, schema=FinalReport, demo=lambda: base)
        except AgentError as exc:
            await ctx.say(
                self.name, f"Model-written report unavailable ({exc}); using deterministic report", type=EventType.ERROR
            )
            return base
        # Facts come from the deterministic report; only prose is taken from the model.
        report.outcome = base.outcome
        report.completed, report.pending, report.attention_required = (
            base.completed,
            base.pending,
            base.attention_required,
        )
        return report
