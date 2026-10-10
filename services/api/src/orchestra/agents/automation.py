"""Automation agent: proposes and (after approval) executes external actions."""

from __future__ import annotations

import re
from typing import Any

from orchestra.agents.base import Agent, AgentContext
from orchestra.db.models import Approval
from orchestra.patching import unified_diff
from orchestra.schemas.agents import (
    ActionType,
    AgentName,
    DebugReport,
    ExecutionPlan,
    Intent,
    ProposedAction,
    ReviewReport,
    VerificationReport,
)
from orchestra.security.injection import InjectionFinding
from orchestra.security.redaction import redact_text

_RISK_ORDER = ["low", "medium", "high"]
FOOTER = "\n\n---\n_Prepared by Orchestra AI. Opened as a draft; a maintainer must review before merging._"


def _slug(text: str, limit: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:limit].strip("-") or "change"


def assess_risk(
    action: ProposedAction,
    *,
    injection: list[InjectionFinding],
    verification: VerificationReport | None,
    review: ReviewReport | None,
) -> tuple[str, list[str]]:
    level = "medium" if action.action_type is ActionType.OPEN_PULL_REQUEST else "low"
    reasons = [f"{action.action_type.value.replace('_', ' ')} writes to GitHub"]

    def bump(to: str, reason: str) -> None:
        nonlocal level
        if _RISK_ORDER.index(to) > _RISK_ORDER.index(level):
            level = to
        reasons.append(reason)

    files = action.payload.get("files", {})
    if len(files) > 5:
        bump("high", f"Touches {len(files)} files")
    if injection:
        bump(
            "high",
            "Untrusted content contained possible prompt-injection attempts: "
            + ", ".join(sorted({f.source for f in injection})),
        )
    if verification is not None and verification.outcome == "unverified" and files:
        bump("medium", "Tests were not executed in the sandbox")
    if review is not None and any(f.severity in {"medium", "high", "critical"} for f in review.findings):
        bump("medium", "Code review raised medium-or-higher findings")
    return level, reasons


class AutomationAgent(Agent):
    name = AgentName.AUTOMATION
    title = "Automation"
    responsibility = (
        "Prepares branches, draft pull requests, issues and comments; executes them only after human approval, "
        "with idempotency keys and recorded outcomes. Never merges, force-pushes or touches the default branch."
    )
    instructions = ""  # deterministic: action content is templated, not model-generated

    def propose(
        self,
        ctx: AgentContext,
        *,
        intent: Intent,
        request: str,
        refs: dict[str, Any],
        original: dict[str, str],
        changed: dict[str, str],
        plan: ExecutionPlan | None,
        debug: DebugReport | None,
        review: ReviewReport | None,
        verification: VerificationReport | None,
        pull_number: int | None,
        blocked_reason: str | None = None,
    ) -> list[ProposedAction]:
        issue = refs.get("issue") or {}
        issue_number = issue.get("number")
        actions: list[ProposedAction] = []

        if intent is Intent.BUG_INVESTIGATION and debug is not None:
            fix_blocked = verification is not None and (verification.outcome == "failed" or blocked_reason is not None)
            if changed and verification is not None and not fix_blocked and debug.proposed_fix:
                fix = debug.proposed_fix
                branch = f"orchestra/fix-{issue_number or _slug(fix.summary)}-{ctx.workflow_id[:6]}"
                checks = "\n".join(
                    f"- {'✅' if c.status == 'passed' else '⏭️' if c.status == 'skipped' else '❌'} "
                    f"{c.name}: {c.details.splitlines()[0] if c.details else ''}"
                    for c in verification.checks
                )
                hypotheses = "\n".join(f"- ({h.confidence:.0%}) {h.root_cause}" for h in debug.hypotheses[:3])
                body = (
                    (f"Refs #{issue_number}\n\n" if issue_number else "")
                    + f"## Summary\n{fix.summary}\n\n## Root cause\n{hypotheses}\n\n## Verification\n{checks}\n\n"
                    + (
                        "## Suggested tests\n" + "\n".join(f"- {t}" for t in fix.tests_to_add) + "\n"
                        if fix.tests_to_add
                        else ""
                    )
                    + FOOTER
                )
                actions.append(
                    ProposedAction(
                        action_type=ActionType.OPEN_PULL_REQUEST,
                        title=f"fix: {fix.summary}"[:120],
                        description=f"Create branch {branch}, commit {len(changed)} file(s), open a draft PR.",
                        payload={
                            "branch": branch,
                            "files": changed,
                            "commit_message": f"fix: {fix.summary}"
                            + (f"\n\nRefs #{issue_number}" if issue_number else ""),
                            "pr_title": f"fix: {fix.summary}"[:120],
                            "pr_body": redact_text(body),
                        },
                        requested_permissions=["contents:write on orchestra/* branch", "pull_requests:write (draft)"],
                    )
                )
            elif issue_number:
                hypotheses = "\n".join(f"- ({h.confidence:.0%}) {h.root_cause}" for h in debug.hypotheses[:3])
                needs = "\n".join(f"- {n}" for n in debug.needs_more_evidence)
                blocked = ""
                if verification is not None and verification.blocking_failures:
                    blocked = "\n\n**A fix was drafted but failed verification:**\n" + "\n".join(
                        f"- {f}" for f in verification.blocking_failures
                    )
                actions.append(
                    ProposedAction(
                        action_type=ActionType.COMMENT_ON_ISSUE,
                        title=f"Post investigation findings on #{issue_number}",
                        description="Comment the ranked hypotheses and requested evidence on the issue.",
                        payload={
                            "number": issue_number,
                            "body": redact_text(
                                f"## Investigation\n{hypotheses}{blocked}"
                                + (f"\n\n## Evidence that would help\n{needs}" if needs else "")
                                + FOOTER
                            ),
                        },
                        requested_permissions=["issues:write (comment)"],
                    )
                )
        elif intent is Intent.FEATURE_PLANNING and plan is not None:
            lines = []
            for t in plan.tasks:
                deps = f" (after {', '.join(t.depends_on)})" if t.depends_on else ""
                lines.append(f"- [ ] **{t.id} {t.title}** — {t.agent.value}, complexity {t.complexity}{deps}")
                lines += [f"  - {c}" for c in t.acceptance_criteria]
            body = (
                f"{plan.summary}\n\n"
                + (f"Refs #{issue_number}\n\n" if issue_number else "")
                + "## Tasks\n"
                + "\n".join(lines)
                + ("\n\n## Risks\n" + "\n".join(f"- {r}" for r in plan.risks) if plan.risks else "")
                + (
                    "\n\n## Open questions\n" + "\n".join(f"- {q}" for q in plan.open_questions)
                    if plan.open_questions
                    else ""
                )
                + FOOTER
            )
            actions.append(
                ProposedAction(
                    action_type=ActionType.CREATE_ISSUE,
                    title=f"Tracking: {issue.get('title') or request.splitlines()[0]}"[:120],
                    description="Create a tracking issue containing the execution plan.",
                    payload={
                        "title": f"Tracking: {issue.get('title') or request.splitlines()[0]}"[:120],
                        "body": redact_text(body),
                        "labels": ["orchestra-plan"],
                    },
                    requested_permissions=["issues:write (create)"],
                )
            )
        elif intent is Intent.CODE_REVIEW and review is not None and pull_number:
            findings = (
                "\n".join(
                    f"- **{f.severity}** `{f.path or 'general'}` — {f.message}"
                    + (f" _Suggestion: {f.suggestion}_" if f.suggestion else "")
                    for f in review.findings
                )
                or "- No findings."
            )
            actions.append(
                ProposedAction(
                    action_type=ActionType.COMMENT_ON_ISSUE,
                    title=f"Post review on PR #{pull_number}",
                    description="Comment the review summary and findings on the pull request.",
                    payload={
                        "number": pull_number,
                        "body": redact_text(
                            f"## Review: {review.verdict.replace('_', ' ')}\n{review.summary}\n\n"
                            f"{findings}\n\n**Tests:** {review.test_coverage}" + FOOTER
                        ),
                    },
                    requested_permissions=["pull_requests:write (comment)"],
                )
            )
        return actions

    @staticmethod
    def proposed_changes(action: ProposedAction, original: dict[str, str]) -> dict[str, Any]:
        if action.action_type is ActionType.OPEN_PULL_REQUEST:
            return {
                "branch": action.payload["branch"],
                "diffs": unified_diff(original, action.payload["files"]),
                "pr_title": action.payload["pr_title"],
                "pr_body": action.payload["pr_body"],
            }
        return {k: v for k, v in action.payload.items()}

    async def execute(self, ctx: AgentContext, approval: Approval, action: ProposedAction) -> dict[str, Any]:
        key = approval.idempotency_key
        if action.action_type is ActionType.OPEN_PULL_REQUEST:
            p = action.payload
            branch = await ctx.tool(
                self.name,
                "github.create_branch",
                {"branch": p["branch"]},
                approval=approval,
                idempotency_key=f"{key}:branch",
            )
            commit = await ctx.tool(
                self.name,
                "github.commit_files",
                {"branch": p["branch"], "files": p["files"], "message": p["commit_message"]},
                approval=approval,
                idempotency_key=f"{key}:commit",
            )
            pull = await ctx.tool(
                self.name,
                "github.open_draft_pull",
                {"head": p["branch"], "title": p["pr_title"], "body": p["pr_body"]},
                approval=approval,
                idempotency_key=f"{key}:pull",
            )
            results = [_as_dict(branch), _as_dict(commit), _as_dict(pull)]
            return {
                "action": action.action_type.value,
                "url": results[-1].get("url"),
                "number": results[-1].get("number"),
                "steps": results,
            }
        if action.action_type is ActionType.CREATE_ISSUE:
            res = _as_dict(
                await ctx.tool(
                    self.name, "github.create_issue", action.payload, approval=approval, idempotency_key=f"{key}:issue"
                )
            )
            return {
                "action": action.action_type.value,
                "url": res.get("url"),
                "number": res.get("number"),
                "steps": [res],
            }
        if action.action_type is ActionType.COMMENT_ON_ISSUE:
            res = _as_dict(
                await ctx.tool(
                    self.name, "github.comment", action.payload, approval=approval, idempotency_key=f"{key}:comment"
                )
            )
            return {
                "action": action.action_type.value,
                "url": res.get("url"),
                "number": res.get("number"),
                "steps": [res],
            }
        raise ValueError(f"Unsupported action {action.action_type}")


def _as_dict(value: Any) -> dict[str, Any]:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else dict(value)
