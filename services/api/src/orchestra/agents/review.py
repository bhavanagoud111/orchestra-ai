"""Code Review agent."""

from __future__ import annotations

import re
from typing import Any

from orchestra.agents.base import Agent, AgentContext
from orchestra.patching import added_lines
from orchestra.schemas.agents import AgentName, FindingCategory, ReviewFinding, ReviewReport, Severity, Verdict
from orchestra.security.redaction import contains_secret

_SEVERITY_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}

# (pattern, severity, category, message, suggestion)
_RULES: list[tuple[re.Pattern[str], Severity, FindingCategory, str, str]] = [
    (
        re.compile(r"\brequests\.(get|post|put|delete|patch)\((?![^)]*timeout)"),
        "high",
        "correctness",
        "Network call without a timeout can hang the caller indefinitely",
        "Pass timeout=... and handle requests.RequestException",
    ),
    (
        re.compile(r"""["']http://(?!localhost|127\.0\.0\.1)"""),
        "medium",
        "security",
        "Plain-HTTP URL: data can be intercepted or modified in transit",
        "Use HTTPS",
    ),
    (
        re.compile(r"\b(eval|exec)\("),
        "high",
        "security",
        "Dynamic code execution on possibly untrusted input",
        "Parse the input explicitly instead of evaluating it",
    ),
    (
        re.compile(r"shell\s*=\s*True"),
        "high",
        "security",
        "subprocess with shell=True enables command injection",
        "Pass an argument list and shell=False",
    ),
    (
        re.compile(r"\bpickle\.loads?\("),
        "high",
        "security",
        "Unpickling untrusted data can execute arbitrary code",
        "Use JSON or a schema-validated format",
    ),
    (
        re.compile(r"\bexcept\s*:\s*$|\bexcept Exception:\s*pass"),
        "medium",
        "maintainability",
        "Bare or swallowed exception hides failures",
        "Catch specific exceptions and log or re-raise",
    ),
    (re.compile(r"\bprint\("), "low", "maintainability", "print() in library code", "Use logging"),
    (re.compile(r"\b(TODO|FIXME|XXX)\b"), "info", "maintainability", "Unresolved TODO in the change", "Track it"),
    (
        re.compile(r"verify\s*=\s*False"),
        "high",
        "security",
        "TLS verification disabled",
        "Keep certificate verification enabled",
    ),
]
_DEF = re.compile(r"^\s*def (\w+)\(([^)]*)\)\s*(->)?")


def static_review(diffs: dict[str, str]) -> list[ReviewFinding]:
    findings: list[ReviewFinding] = []
    for path, diff in diffs.items():
        for line in added_lines(diff):
            if contains_secret(line):
                findings.append(
                    ReviewFinding(
                        severity="critical",
                        category="security",
                        path=path,
                        message="Change appears to add a credential",
                        suggestion="Remove it and rotate the credential",
                    )
                )
            for pattern, sev, cat, msg, sug in _RULES:
                if pattern.search(line):
                    findings.append(ReviewFinding(severity=sev, category=cat, path=path, message=msg, suggestion=sug))
            if (
                path.endswith(".py")
                and (m := _DEF.match(line))
                and not m.group(3)
                and not m.group(1).startswith("test")
            ):
                findings.append(
                    ReviewFinding(
                        severity="low",
                        category="maintainability",
                        path=path,
                        message=f"New function {m.group(1)}() has no return type annotation",
                        suggestion="Add type hints",
                    )
                )
    source = [p for p in diffs if not re.search(r"(^|/)tests?/|test_|_test\.|\.test\.|\.spec\.", p)]
    tests = [p for p in diffs if p not in source]
    if source and not tests:
        findings.append(
            ReviewFinding(
                severity="medium",
                category="tests",
                path=None,
                message="Source files changed without accompanying test changes",
                suggestion="Add a regression test covering the changed behavior",
            )
        )
    # de-duplicate identical findings per file
    unique: dict[tuple[str | None, str], ReviewFinding] = {}
    for f in findings:
        unique.setdefault((f.path, f.message), f)
    return sorted(unique.values(), key=lambda f: -_SEVERITY_ORDER[f.severity])


def verdict_for(findings: list[ReviewFinding]) -> Verdict:
    worst = max((_SEVERITY_ORDER[f.severity] for f in findings), default=0)
    return "request_changes" if worst >= 3 else ("comment" if worst >= 2 else "approve")


def blocking(findings: list[ReviewFinding]) -> list[ReviewFinding]:
    return [f for f in findings if _SEVERITY_ORDER[f.severity] >= 3]


class CodeReviewAgent(Agent):
    name = AgentName.CODE_REVIEW
    title = "Code Review"
    responsibility = (
        "Reviews proposed or submitted changes for correctness risks, security problems, test coverage and "
        "maintainability, and produces actionable review feedback."
    )
    instructions = (
        "Review the diff. Report concrete findings only, each tied to a file, with severity "
        "(info/low/medium/high/critical) and a suggested fix. Use high or critical only for defects that would "
        "cause incorrect behavior, security exposure, or data loss. Assess whether tests cover the change. "
        "Static-analysis findings are provided; confirm, refine, or dismiss them with reasons."
    )

    async def review(
        self, ctx: AgentContext, *, subject: str, diffs: dict[str, str], extra_context: dict[str, Any] | None = None
    ) -> ReviewReport:
        static = static_review(diffs)
        await ctx.say(
            self.name,
            f"Static analysis: {len(static)} findings across {len(diffs)} files",
            payload={"files": list(diffs)},
        )

        def demo() -> ReviewReport:
            return ReviewReport(
                verdict=verdict_for(static),
                summary=f"Reviewed {len(diffs)} file(s) for {subject}: "
                + (f"{len(blocking(static))} blocking issue(s)." if blocking(static) else "no blocking issues."),
                findings=static,
                test_coverage="Change includes tests."
                if any("test" in p for p in diffs)
                else "No tests accompany this change.",
            )

        context = ctx.context().add("Subject", subject, priority=100)
        for path, diff in diffs.items():
            context.add_untrusted(f"Diff {path}", diff, source=path, priority=90)
        context.add("Static analysis findings", "\n".join(f.model_dump_json() for f in static) or "none", priority=80)
        if extra_context:
            context.add("Context", str(extra_context)[:8000], priority=50)
        report = await self.reason(ctx, context=context, schema=ReviewReport, demo=demo)
        # Static security findings cannot be dismissed by the model.
        kept = {(f.path, f.message) for f in report.findings}
        for f in static:
            if f.category == "security" and _SEVERITY_ORDER[f.severity] >= 3 and (f.path, f.message) not in kept:
                report.findings.append(f)
        if blocking(report.findings) and report.verdict == "approve":
            report.verdict = "request_changes"
        return report
