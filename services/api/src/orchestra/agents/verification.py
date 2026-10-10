"""Verification agent: decides whether a proposed change may proceed."""

from __future__ import annotations

import ast
import json

from orchestra.agents.base import Agent, AgentContext
from orchestra.agents.review import blocking
from orchestra.config import RunMode
from orchestra.patching import PatchResult, unified_diff
from orchestra.retrieval import TEXT_EXTENSIONS
from orchestra.schemas.agents import (
    AcceptanceAssessment,
    AgentName,
    CheckResult,
    DebugReport,
    ExecutionPlan,
    ReviewReport,
    VerificationOutcome,
    VerificationReport,
)
from orchestra.tools.executor import ToolError


def syntax_checks(files: dict[str, str]) -> list[CheckResult]:
    checks: list[CheckResult] = []
    for path, content in files.items():
        try:
            if path.endswith(".py"):
                ast.parse(content, filename=path)
            elif path.endswith(".json"):
                json.loads(content)
            else:
                continue
        except (SyntaxError, ValueError) as exc:
            checks.append(
                CheckResult(
                    name=f"syntax:{path}",
                    kind="syntax",
                    status="failed",
                    required=True,
                    details=f"{exc.__class__.__name__}: {exc}",
                )
            )
        else:
            checks.append(
                CheckResult(
                    name=f"syntax:{path}", kind="syntax", status="passed", required=True, details="Parses successfully"
                )
            )
    return checks


class VerificationAgent(Agent):
    name = AgentName.VERIFICATION
    title = "Verification"
    responsibility = (
        "Applies proposed changes in isolation, runs permitted checks and tests in a sandbox, validates "
        "acceptance criteria, reports unresolved failures, and blocks completion when required checks fail."
    )
    instructions = (
        "For each acceptance criterion, decide whether the evidence (diff, check results, test output) shows "
        "it is satisfied. Use status 'passed' only when the evidence demonstrates it, 'failed' when the "
        "evidence contradicts it, and 'skipped' when it cannot be judged from the evidence. Mark kind as "
        "'acceptance' and required as true."
    )

    async def verify(
        self,
        ctx: AgentContext,
        *,
        original: dict[str, str],
        patch: PatchResult,
        debug: DebugReport | None,
        review: ReviewReport | None,
        plan: ExecutionPlan | None,
    ) -> VerificationReport:
        checks: list[CheckResult] = []
        if debug is None or debug.proposed_fix is None:
            report = VerificationReport(
                outcome="unverified",
                checks=[
                    CheckResult(
                        name="proposed-fix",
                        kind="patch",
                        status="skipped",
                        required=False,
                        details="No fix was proposed, so there is nothing to verify",
                    )
                ],
                blocking_failures=[],
            )
            await ctx.say(self.name, "Nothing to verify: no fix proposed")
            return report

        checks.append(
            CheckResult(
                name="patch-applies",
                kind="patch",
                status="passed" if not patch.errors else "failed",
                required=True,
                details="All edits apply exactly once" if not patch.errors else "; ".join(patch.errors),
            )
        )
        checks.append(
            CheckResult(
                name="change-policy",
                kind="policy",
                status="passed" if not patch.policy_violations else "failed",
                required=True,
                details="Edits respect path and content policy"
                if not patch.policy_violations
                else "; ".join(patch.policy_violations),
            )
        )
        if patch.ok and not patch.files:
            checks.append(
                CheckResult(
                    name="non-empty-change",
                    kind="patch",
                    status="failed",
                    required=True,
                    details="Edits do not change any file",
                )
            )
        checks.extend(syntax_checks(patch.files))

        if review is not None:
            blockers = blocking(review.findings)
            checks.append(
                CheckResult(
                    name="review-gate",
                    kind="policy",
                    status="failed" if blockers else "passed",
                    required=True,
                    details="; ".join(f"{f.severity}: {f.message}" for f in blockers)
                    or f"Review verdict: {review.verdict}",
                )
            )

        # Tests run only in the sandbox, and only when the static checks passed.
        static_ok = all(c.status != "failed" for c in checks if c.required)
        if ctx.runtime.sandbox is not None and static_ok:
            snapshot = await self._snapshot(ctx, original, patch.files)
            if snapshot is None:
                checks.append(
                    CheckResult(
                        name="tests",
                        kind="test",
                        status="skipped",
                        required=False,
                        details="Repository too large to snapshot into the sandbox",
                    )
                )
            else:
                try:
                    run = await ctx.tool(self.name, "sandbox.run_tests", {"files": snapshot})
                    checks.append(
                        CheckResult(
                            name="tests",
                            kind="test",
                            status="passed" if run.status == "passed" else "failed",
                            required=True,
                            details=f"`{run.command}` → {run.status} (exit {run.exit_code}, {run.duration_s}s)\n"
                            f"{run.stdout_tail[-1500:]}",
                        )
                    )
                except ToolError as exc:
                    checks.append(
                        CheckResult(
                            name="tests",
                            kind="test",
                            status="skipped",
                            required=False,
                            details=f"Sandbox run failed to start: {exc}",
                        )
                    )
        else:
            reason = (
                "sandbox disabled (set ORCHESTRA_SANDBOX_ENABLED=true on a host with Docker)"
                if ctx.runtime.sandbox is None
                else "static checks failed"
            )
            checks.append(
                CheckResult(
                    name="tests", kind="test", status="skipped", required=False, details=f"Tests not executed: {reason}"
                )
            )

        try:
            ci = await ctx.tool(self.name, "github.ci_status", {"ref": ctx.runtime.default_branch})
            checks.append(
                CheckResult(
                    name="baseline-ci",
                    kind="test",
                    status="passed" if ci.state == "success" else "skipped",
                    required=False,
                    details=f"CI on {ctx.runtime.default_branch}: {ci.state}",
                )
            )
        except ToolError as exc:
            checks.append(
                CheckResult(
                    name="baseline-ci",
                    kind="test",
                    status="skipped",
                    required=False,
                    details=f"CI status unavailable: {exc}",
                )
            )

        criteria = [c for t in (plan.tasks if plan else []) for c in t.acceptance_criteria]
        if ctx.mode is RunMode.LIVE and criteria and static_ok:
            context = (
                ctx.context()
                .add("Acceptance criteria", "\n".join(f"- {c}" for c in criteria), priority=100)
                .add("Check results", "\n".join(c.model_dump_json() for c in checks), priority=90)
            )
            for path, diff in unified_diff(original, patch.files).items():
                context.add_untrusted(f"Diff {path}", diff, source=path, priority=80)
            assessment = await self.reason(
                ctx, context=context, schema=AcceptanceAssessment, demo=lambda: AcceptanceAssessment(results=[])
            )
            for r in assessment.results:
                checks.append(r.model_copy(update={"kind": "acceptance", "required": True}))
        elif criteria:
            checks.append(
                CheckResult(
                    name="acceptance-criteria",
                    kind="acceptance",
                    status="skipped",
                    required=False,
                    details="Acceptance criteria are judged by the model in live mode; demo mode "
                    "verifies patch, policy, syntax, review and tests only.",
                )
            )

        failures = [
            f"{c.name}: {c.details.splitlines()[0] if c.details else c.status}"
            for c in checks
            if c.required and c.status == "failed"
        ]
        tests_ran = any(c.kind == "test" and c.name == "tests" and c.status == "passed" for c in checks)
        outcome: VerificationOutcome = "failed" if failures else ("passed" if tests_ran else "unverified")
        feedback = None
        if failures:
            feedback = "The previous proposal failed verification:\n" + "\n".join(f"- {f}" for f in failures)
        report = VerificationReport(
            outcome=outcome, checks=checks, blocking_failures=failures, feedback_for_debugger=feedback
        )
        await ctx.say(
            self.name,
            f"Verification {outcome}: {sum(c.status == 'passed' for c in checks)} passed, {len(failures)} blocking",
            payload={"outcome": outcome, "failures": failures},
        )
        return report

    async def _snapshot(
        self, ctx: AgentContext, original: dict[str, str], changed: dict[str, str]
    ) -> dict[str, str] | None:
        tree = await ctx.runtime.reader.tree()
        text_files = [e.path for e in tree if "." + e.path.rsplit(".", 1)[-1] in TEXT_EXTENSIONS]
        if len(text_files) > ctx.settings.retrieval_max_files:
            return None
        files: dict[str, str] = {}
        for path in text_files:
            content = original.get(path) or await ctx.runtime.reader.read(path)
            if content is not None:
                files[path] = content
        files.update(changed)
        return files
