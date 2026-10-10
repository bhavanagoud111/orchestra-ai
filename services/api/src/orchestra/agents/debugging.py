"""Debugging agent: root-cause analysis and fix proposals."""

from __future__ import annotations

import re
from typing import Any

from orchestra.agents.base import Agent, AgentContext
from orchestra.memory.store import MemoryKind
from orchestra.retrieval import TraceFrame, error_signature, parse_stack_trace
from orchestra.schemas.agents import AgentName, DebugReport, ExecutionPlan, FileEdit, Hypothesis, ProposedFix

_DIVISION_RETURN = re.compile(r"^(?P<indent>\s*)return (?P<num>.+?)\s*/\s*(?P<den>[\w.\[\]()]+)\s*$")


def _match_frame(frames: list[TraceFrame], known: set[str]) -> TraceFrame | None:
    for frame in reversed(frames):  # deepest frame first
        for path in known:
            if path == frame.path or path.endswith("/" + frame.path) or frame.path.endswith("/" + path):
                return TraceFrame(path, frame.line, frame.function)
    return None


def heuristic_debug(evidence: str, frames: list[TraceFrame], files: dict[str, str]) -> DebugReport:
    signature = error_signature(evidence)
    frame = _match_frame(frames, set(files))
    if frame is None:
        return DebugReport(
            error_signature=signature,
            hypotheses=[
                Hypothesis(root_cause="Insufficient evidence to localise the failure", evidence=[], confidence=0.1)
            ],
            proposed_fix=None,
            needs_more_evidence=["A full stack trace", "Steps or input that reproduce the failure"],
        )
    lines = files[frame.path].splitlines()
    idx = min(max(frame.line - 1, 0), len(lines) - 1) if lines else 0
    line = lines[idx] if lines else ""
    where = f"{frame.path}:{frame.line} in {frame.function or '?'}()"

    if signature and signature.startswith("ZeroDivisionError") and (m := _DIVISION_RETURN.match(line)):
        indent, den = m.group("indent"), m.group("den")
        replacement = f"{indent}if not {den}:\n{indent}    return 0.0\n{line.rstrip()}"
        occurrences = files[frame.path].count(line)
        fix = None
        if occurrences == 1:
            fix = ProposedFix(
                summary=f"Return 0.0 from {frame.function or 'the function'} when {den} is zero",
                edits=[
                    FileEdit(
                        path=frame.path,
                        find=line,
                        replace=replacement,
                        rationale=f"Guard the division by {den}, which is zero for empty inputs",
                    )
                ],
                tests_to_add=[
                    f"Regression test: {frame.function or 'function'} returns 0.0 when {den} is 0",
                    "Summary for an empty input returns zero totals instead of raising",
                ],
            )
        return DebugReport(
            error_signature=signature,
            hypotheses=[
                Hypothesis(
                    root_cause=f"Unguarded division by {den} at {where}; {den} is zero for empty inputs",
                    evidence=[f"Stack trace terminates at {where}", f"Line: {line.strip()}"],
                    confidence=0.9,
                ),
                Hypothesis(
                    root_cause="Caller should not request this value for empty inputs",
                    evidence=["Callers higher in the trace pass the empty input through unchanged"],
                    confidence=0.25,
                ),
            ],
            proposed_fix=fix,
            needs_more_evidence=[] if fix else ["The division line appears more than once; need a narrower edit"],
        )

    return DebugReport(
        error_signature=signature,
        hypotheses=[
            Hypothesis(root_cause=f"Failure raised at {where}", evidence=[f"Line: {line.strip()}"], confidence=0.4)
        ],
        proposed_fix=None,
        needs_more_evidence=["Input values at the failing line", "A failing test that reproduces the error"],
    )


class DebuggingAgent(Agent):
    name = AgentName.DEBUGGING
    title = "Debugging"
    responsibility = (
        "Analyses error logs, stack traces and failing tests, ranks root-cause hypotheses, proposes minimal "
        "fixes, and asks for more evidence when confidence is low."
    )
    instructions = (
        "Diagnose the failure. Rank hypotheses by confidence, each with concrete evidence from the code or "
        "trace. If confident, propose a minimal fix as search/replace edits: `find` must be copied exactly from "
        "the current file content (including indentation) and occur exactly once; use an empty `find` only to "
        "create a new file. Do not modify CI configuration, credentials, or unrelated files. If evidence is "
        "insufficient, set proposed_fix to null and list what is needed."
    )

    async def investigate(
        self,
        ctx: AgentContext,
        request: str,
        refs: dict[str, Any],
        snippets: list[dict[str, Any]],
        plan: ExecutionPlan | None,
        feedback: str | None,
    ) -> DebugReport:
        issue = refs.get("issue") or {}
        evidence = f"{request}\n{issue.get('body') or ''}"
        frames = parse_stack_trace(evidence)
        known = {e.path for e in await ctx.runtime.reader.tree()}
        files: dict[str, str] = {}
        for frame in frames:
            match = _match_frame([frame], known)
            if match and match.path not in files:
                result = await ctx.tool(self.name, "repo.read_file", {"path": match.path})
                if result.content is not None and result.start_line == 1 and result.end_line >= result.total_lines:
                    files[match.path] = result.content
        for s in snippets[:4]:
            if s["path"] not in files and len(files) < 6:
                result = await ctx.tool(self.name, "repo.read_file", {"path": s["path"]})
                if result.content is not None and result.end_line >= result.total_lines:
                    files[s["path"]] = result.content
        memories = await ctx.tool(
            self.name,
            "memory.recall",
            {
                "query": error_signature(evidence) or evidence[:2000],
                "kinds": [MemoryKind.ERROR_INVESTIGATION.value, MemoryKind.FAILURE_PATTERN.value],
                "limit": 3,
            },
        )

        context = ctx.context().add("Engineering request", request, priority=100)
        if issue:
            context.add_untrusted(
                f"Issue #{issue.get('number')}",
                issue.get("body") or "",
                source=f"issue #{issue.get('number')}",
                priority=95,
            )
        if feedback:
            context.add("Verification feedback on your previous proposal", feedback, priority=98)
        if plan:
            context.add("Plan", plan.model_dump_json(), priority=50)
        for path, content in files.items():
            numbered = "\n".join(f"{i + 1:>4}| {ln}" for i, ln in enumerate(content.splitlines()))
            context.add_untrusted(
                f"File {path} (line numbers are for reference, not part of the content)",
                numbered,
                source=path,
                priority=90,
            )
        for m in memories.hits:
            context.add(f"Similar past investigation (similarity {m.score:.2f})", m.content, priority=45)

        report = await self.reason(
            ctx, context=context, schema=DebugReport, demo=lambda: heuristic_debug(evidence, frames, files)
        )
        if report.proposed_fix is not None:
            await ctx.say(
                self.name,
                f"Proposed fix: {report.proposed_fix.summary}",
                payload={"edits": [e.path for e in report.proposed_fix.edits]},
            )
        elif report.needs_more_evidence:
            await ctx.say(self.name, "Requesting more evidence: " + "; ".join(report.needs_more_evidence))
        return report
