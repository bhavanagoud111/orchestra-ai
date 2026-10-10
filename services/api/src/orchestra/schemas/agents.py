"""Structured outputs produced by agents.

These models are passed to the LLM as output schemas in live mode and are
produced by deterministic heuristics in demo mode. Either way, every agent
output is validated against these models before it enters workflow state.
Schemas intentionally avoid numeric/length constraints that structured-output
backends may not enforce; range checks happen in validators instead.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class AgentName(StrEnum):
    ORCHESTRATOR = "orchestrator"
    REPOSITORY = "repository_intelligence"
    PLANNING = "planning"
    DEBUGGING = "debugging"
    CODE_REVIEW = "code_review"
    AUTOMATION = "automation"
    VERIFICATION = "verification"
    REPORTING = "reporting"


class Intent(StrEnum):
    BUG_INVESTIGATION = "bug_investigation"
    FEATURE_PLANNING = "feature_planning"
    CODE_REVIEW = "code_review"
    REPOSITORY_ANALYSIS = "repository_analysis"


Severity = Literal["info", "low", "medium", "high", "critical"]
FindingCategory = Literal["correctness", "security", "tests", "maintainability", "performance"]
Verdict = Literal["approve", "request_changes", "comment"]
VerificationOutcome = Literal["passed", "failed", "unverified"]
ReportOutcome = Literal["completed", "blocked", "partial", "failed"]


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


# --- Orchestrator -----------------------------------------------------------------


class IntentClassification(BaseModel):
    intent: Intent
    confidence: float = Field(description="0.0-1.0")
    rationale: str
    issue_number: int | None = Field(default=None, description="Referenced GitHub issue number, if any")
    pull_number: int | None = Field(default=None, description="Referenced pull request number, if any")
    search_terms: list[str] = Field(description="Identifiers, file names, or error strings worth searching for")

    @field_validator("confidence")
    @classmethod
    def _confidence(cls, v: float) -> float:
        return _clamp01(v)


# --- Repository intelligence ---------------------------------------------------------


class RelevantFile(BaseModel):
    path: str
    reason: str
    score: float
    start_line: int | None = None
    end_line: int | None = None


class RepositoryContext(BaseModel):
    summary: str = Field(description="What the repository does and how the relevant code is organised")
    languages: list[str]
    dependencies: list[str]
    entry_points: list[str]
    relevant_files: list[RelevantFile]
    code_relationships: list[str] = Field(description="Short statements like 'a.py calls b.compute()'")
    documentation: list[str] = Field(description="Paths of relevant docs")
    risks: list[str] = Field(default_factory=list)


# --- Planning ------------------------------------------------------------------------


class PlannedTask(BaseModel):
    id: str = Field(description="Short id such as T1")
    title: str
    description: str
    agent: AgentName
    depends_on: list[str]
    complexity: int = Field(description="1 (trivial) to 5 (very complex)")
    acceptance_criteria: list[str]
    required_tools: list[str]
    required_permissions: list[str]

    @field_validator("complexity")
    @classmethod
    def _complexity(cls, v: int) -> int:
        return max(1, min(5, int(v)))


class ExecutionPlan(BaseModel):
    summary: str
    tasks: list[PlannedTask]
    risks: list[str]
    open_questions: list[str]

    @field_validator("tasks")
    @classmethod
    def _dag(cls, tasks: list[PlannedTask]) -> list[PlannedTask]:
        ids = {t.id for t in tasks}
        if len(ids) != len(tasks):
            raise ValueError("task ids must be unique")
        for t in tasks:
            unknown = set(t.depends_on) - ids
            if unknown:
                raise ValueError(f"task {t.id} depends on unknown tasks {sorted(unknown)}")
        # reject cycles
        graph = {t.id: set(t.depends_on) for t in tasks}
        visiting: set[str] = set()
        done: set[str] = set()

        def visit(node: str) -> None:
            if node in done:
                return
            if node in visiting:
                raise ValueError(f"dependency cycle through {node}")
            visiting.add(node)
            for dep in graph[node]:
                visit(dep)
            visiting.discard(node)
            done.add(node)

        for node in graph:
            visit(node)
        return tasks


# --- Debugging -----------------------------------------------------------------------


class Hypothesis(BaseModel):
    root_cause: str
    evidence: list[str]
    confidence: float = Field(description="0.0-1.0")

    @field_validator("confidence")
    @classmethod
    def _confidence(cls, v: float) -> float:
        return _clamp01(v)


class FileEdit(BaseModel):
    """A search/replace edit. ``find`` must match the current file content exactly once."""

    path: str
    find: str = Field(description="Exact existing text to replace (must occur exactly once)")
    replace: str
    rationale: str


class ProposedFix(BaseModel):
    summary: str
    edits: list[FileEdit]
    tests_to_add: list[str]


class DebugReport(BaseModel):
    error_signature: str | None
    hypotheses: list[Hypothesis]
    proposed_fix: ProposedFix | None
    needs_more_evidence: list[str] = Field(description="Specific evidence that would raise confidence")


# --- Code review ---------------------------------------------------------------------


class ReviewFinding(BaseModel):
    severity: Severity
    category: FindingCategory
    path: str | None
    message: str
    suggestion: str | None


class ReviewReport(BaseModel):
    verdict: Verdict
    summary: str
    findings: list[ReviewFinding]
    test_coverage: str


# --- Verification --------------------------------------------------------------------


class CheckResult(BaseModel):
    name: str
    kind: Literal["patch", "syntax", "test", "acceptance", "policy"]
    status: Literal["passed", "failed", "skipped"]
    required: bool
    details: str


class VerificationReport(BaseModel):
    outcome: VerificationOutcome
    checks: list[CheckResult]
    blocking_failures: list[str]
    feedback_for_debugger: str | None = None


class AcceptanceAssessment(BaseModel):
    """LLM-judged acceptance-criteria assessment used inside verification."""

    results: list[CheckResult]


# --- Automation ----------------------------------------------------------------------


class ActionType(StrEnum):
    OPEN_PULL_REQUEST = "open_pull_request"
    CREATE_ISSUE = "create_issue"
    COMMENT_ON_ISSUE = "comment_on_issue"


class ProposedAction(BaseModel):
    action_type: ActionType
    title: str
    description: str
    payload: dict = Field(default_factory=dict)
    requested_permissions: list[str]


# --- Reporting -----------------------------------------------------------------------


class AgentDecision(BaseModel):
    agent: AgentName
    decision: str
    rationale: str


class FinalReport(BaseModel):
    headline: str
    outcome: ReportOutcome
    summary: str
    completed: list[str]
    pending: list[str]
    decisions: list[AgentDecision]
    attention_required: list[str]
