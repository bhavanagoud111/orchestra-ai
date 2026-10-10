"""Orchestrator agent: interprets requests and selects the workflow route."""

from __future__ import annotations

import re
from typing import Any

from orchestra.agents.base import Agent, AgentContext
from orchestra.integrations.github.protocol import IssueInfo, PullInfo
from orchestra.retrieval import error_signature, extract_terms, parse_stack_trace
from orchestra.schemas.agents import AgentName, Intent, IntentClassification
from orchestra.tools.executor import ToolError

_PULL_REF = re.compile(r"(?i)\b(?:pr|pull request|pull)\s*#?(\d+)")
_ISSUE_REF = re.compile(r"(?i)(?:\bissue\s*#?(\d+)|(?<![\w/])#(\d+))")

# Stages each intent runs after context retrieval and planning. Used for routing and shown in the UI.
ROUTES: dict[Intent, list[str]] = {
    Intent.BUG_INVESTIGATION: ["debug", "review", "verify", "propose_actions", "approval_gate", "execute_actions"],
    Intent.FEATURE_PLANNING: ["propose_actions", "approval_gate", "execute_actions"],
    Intent.CODE_REVIEW: ["review", "propose_actions", "approval_gate", "execute_actions"],
    Intent.REPOSITORY_ANALYSIS: [],
}


def find_references(text: str) -> tuple[int | None, int | None]:
    pull = _PULL_REF.search(text)
    pull_number = int(pull.group(1)) if pull else None
    issue_number = None
    for m in _ISSUE_REF.finditer(text):
        n = int(m.group(1) or m.group(2))
        if n != pull_number:
            issue_number = n
            break
    return issue_number, pull_number


def heuristic_intent(text: str, pull_number: int | None, issue: IssueInfo | None) -> tuple[Intent, float, str]:
    lowered = text.lower()
    labels = {lbl.lower() for lbl in (issue.labels if issue else [])}
    if pull_number is not None or re.search(r"\breview\b", lowered):
        return Intent.CODE_REVIEW, 0.85, "Request references a pull request or asks for a review."
    if (
        parse_stack_trace(text)
        or error_signature(text)
        or "bug" in labels
        or re.search(
            r"\b(bugs?|crash(es|ed|ing)?|exceptions?|traceback|fail(s|ed|ing|ure)?|broken|break(s|ing)?|errors?|"
            r"regressions?|500|not working|doesn't work)\b",
            lowered,
        )
    ):
        return Intent.BUG_INVESTIGATION, 0.8, "Request describes a failure, error, or bug report."
    if (
        "enhancement" in labels
        or "feature" in labels
        or re.search(r"\b(add|implement|support|build|plan|design|introduce|feature)\b", lowered)
    ):
        return Intent.FEATURE_PLANNING, 0.75, "Request asks for new functionality to be planned."
    return Intent.REPOSITORY_ANALYSIS, 0.6, "No failure or change request detected; defaulting to analysis."


class OrchestratorAgent(Agent):
    name = AgentName.ORCHESTRATOR
    title = "Orchestrator"
    responsibility = (
        "Interprets engineering requests, selects the workflow route, assigns work to specialist agents, "
        "tracks state, handles retries, and escalates risky actions to a human."
    )
    instructions = (
        "Classify the engineering request into exactly one intent: bug_investigation (something is broken), "
        "feature_planning (new capability to plan), code_review (review a pull request or diff), or "
        "repository_analysis (understand a codebase). Extract referenced issue/PR numbers and up to 15 "
        "search terms (identifiers, file names, error types) that would locate relevant code."
    )

    async def classify(self, ctx: AgentContext, request: str) -> tuple[IntentClassification, dict[str, Any]]:
        issue_number, pull_number = find_references(request)
        issue: IssueInfo | None = None
        pull: PullInfo | None = None
        if issue_number is not None:
            try:
                issue = await ctx.tool(self.name, "github.get_issue", {"number": issue_number})
            except ToolError as exc:
                await ctx.say(self.name, f"Referenced issue #{issue_number} could not be loaded: {exc}")
        if pull_number is not None:
            try:
                pull = (await ctx.tool(self.name, "github.get_pull", {"number": pull_number})).pull
            except ToolError as exc:
                await ctx.say(self.name, f"Referenced pull request #{pull_number} could not be loaded: {exc}")

        evidence = request
        if issue is not None:
            evidence += f"\n\n{issue.title}\n{issue.body or ''}"
            await ctx.screen(self.name, f"{issue.title}\n{issue.body or ''}", f"issue #{issue.number}")
        if pull is not None:
            await ctx.screen(self.name, f"{pull.title}\n{pull.body or ''}", f"pull request #{pull.number}")

        def demo() -> IntentClassification:
            intent, confidence, rationale = heuristic_intent(evidence, pull_number, issue)
            return IntentClassification(
                intent=intent,
                confidence=confidence,
                rationale=rationale,
                issue_number=issue_number,
                pull_number=pull_number,
                search_terms=extract_terms(evidence, limit=15),
            )

        context = ctx.context().add("Engineering request", request, priority=100)
        if issue is not None:
            context.add_untrusted(
                f"Issue #{issue.number}: {issue.title}", issue.body or "", source=f"issue #{issue.number}", priority=80
            )
        if pull is not None:
            context.add_untrusted(
                f"Pull request #{pull.number}: {pull.title}",
                pull.body or "",
                source=f"pull request #{pull.number}",
                priority=80,
            )
        result = await self.reason(ctx, context=context, schema=IntentClassification, demo=demo)
        # Explicit references found by deterministic parsing win over model extraction.
        result.issue_number = issue_number if issue_number is not None else result.issue_number
        result.pull_number = pull_number if pull_number is not None else result.pull_number
        if not result.search_terms:
            result.search_terms = extract_terms(evidence, limit=15)
        if result.intent is Intent.CODE_REVIEW and result.pull_number is None:
            result.rationale += " No pull request was referenced, so the review covers proposed changes only."

        refs = {
            "issue": issue.model_dump(mode="json") if issue else None,
            "pull": pull.model_dump(mode="json") if pull else None,
        }
        return result, refs
