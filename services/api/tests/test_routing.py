"""Unit tests for intent classification, routing functions, and plan validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from orchestra.agents.orchestrator import ROUTES, find_references, heuristic_intent
from orchestra.integrations.github.protocol import IssueInfo
from orchestra.schemas.agents import AgentName, ExecutionPlan, Intent, PlannedTask
from orchestra.workflows.graph import graph_topology


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Review PR #9 before merge", Intent.CODE_REVIEW),
        ("Traceback ... ZeroDivisionError: division by zero", Intent.BUG_INVESTIGATION),
        ("Checkout crashes when the cart is empty", Intent.BUG_INVESTIGATION),
        ("Add support for fixed discount codes", Intent.FEATURE_PLANNING),
        ("Give me an overview of the modules", Intent.REPOSITORY_ANALYSIS),
    ],
)
def test_heuristic_intent(text: str, expected: Intent) -> None:
    _, pull = find_references(text)
    assert heuristic_intent(text, pull, None)[0] is expected


def test_issue_labels_drive_intent() -> None:
    issue = IssueInfo(number=3, title="x", state="open", labels=["bug"])
    assert heuristic_intent("look at #3", None, issue)[0] is Intent.BUG_INVESTIGATION


@pytest.mark.parametrize(
    ("text", "issue", "pull"),
    [("Investigate issue #12", 12, None), ("Review PR #9", None, 9), ("pull request 4 and #7", 7, 4),
     ("no references", None, None), ("see owner/repo#5", None, None)],
)
def test_find_references(text: str, issue: int | None, pull: int | None) -> None:
    assert find_references(text) == (issue, pull)


def _state(intent: Intent, **kw):
    return {"intent": {"intent": intent.value}, **kw}


def test_routes_cover_every_intent() -> None:
    assert set(ROUTES) == set(Intent)
    assert ROUTES[Intent.REPOSITORY_ANALYSIS] == []
    assert "approval_gate" in ROUTES[Intent.BUG_INVESTIGATION]


def test_conditional_routing() -> None:
    from orchestra.workflows import graph as g

    assert g.after_dispatch(_state(Intent.BUG_INVESTIGATION)) == "debug"
    assert g.after_dispatch(_state(Intent.CODE_REVIEW)) == "review"
    assert g.after_dispatch(_state(Intent.FEATURE_PLANNING)) == "propose_actions"
    assert g.after_dispatch(_state(Intent.REPOSITORY_ANALYSIS)) == "report"
    assert g.after_debug({"changed_files": {"a.py": "x"}}) == "review"
    assert g.after_debug({"changed_files": {}}) == "verify"
    assert g.after_review(_state(Intent.BUG_INVESTIGATION)) == "verify"
    assert g.after_review(_state(Intent.CODE_REVIEW)) == "propose_actions"
    # failed verification loops back to debugging until blocked
    assert g.after_verify({"verification": {"outcome": "failed"}}) == "debug"
    assert g.after_verify({"verification": {"outcome": "failed"}, "blocked_reason": "x"}) == "propose_actions"
    assert g.after_verify({"verification": {"outcome": "passed"}}) == "propose_actions"
    assert g.after_propose({"proposed_actions": [{}]}) == "approval_gate"
    assert g.after_propose({"proposed_actions": []}) == "report"


def test_graph_topology_has_terminal_path() -> None:
    topo = graph_topology()
    ids = {n["id"] for n in topo["nodes"]}
    assert {"__start__", "__end__", "classify", "approval_gate", "report"} <= ids
    assert {"source": "report", "target": "__end__", "conditional": False} in topo["edges"]
    # the only cycle is the bounded debug <-> verify revision loop
    assert any(e["source"] == "verify" and e["target"] == "debug" for e in topo["edges"])


def _task(id: str, deps: list[str]) -> PlannedTask:
    return PlannedTask(id=id, title=id, description="", agent=AgentName.PLANNING, depends_on=deps, complexity=9,
                       acceptance_criteria=[], required_tools=[], required_permissions=[])


def test_plan_rejects_cycles_and_unknown_dependencies() -> None:
    with pytest.raises(ValidationError, match="cycle"):
        ExecutionPlan(summary="", tasks=[_task("T1", ["T2"]), _task("T2", ["T1"])], risks=[], open_questions=[])
    with pytest.raises(ValidationError, match="unknown"):
        ExecutionPlan(summary="", tasks=[_task("T1", ["T9"])], risks=[], open_questions=[])
    plan = ExecutionPlan(summary="", tasks=[_task("T1", []), _task("T2", ["T1"])], risks=[], open_questions=[])
    assert plan.tasks[0].complexity == 5  # clamped
