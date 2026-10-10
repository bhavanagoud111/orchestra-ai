"""Workflow state transitions, cancellation, execution limits, persistence and live-mode execution."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from orchestra.agents.reasoner import ReasonerError
from orchestra.api.app import create_app
from orchestra.db.models import Approval, Workflow, WorkflowEvent
from orchestra.services import Services
from orchestra.settings_store import LimitSettings, SettingsUpdate
from tests.conftest import (
    ScriptedReasoner,
    auth,
    connect_demo,
    live_settings,
    make_settings,
    submit,
    wait_for_status,
)


async def _statuses(services: Services, wf_id: str) -> list[str]:
    async with services.db.session() as s:
        rows = (await s.execute(select(WorkflowEvent).where(
            WorkflowEvent.workflow_id == wf_id, WorkflowEvent.type == "status_changed")
            .order_by(WorkflowEvent.seq))).scalars().all()
    return [r.payload["to"] for r in rows]


async def test_status_transitions_through_approval(client, services: Services) -> None:
    repo_id = await connect_demo(client)
    wf_id = await submit(client, repo_id, "Investigate issue #12")
    wf = await wait_for_status(client, wf_id, {"waiting_for_approval"})
    await client.post(f"/api/approvals/{wf['approvals'][0]['id']}/approve", headers=auth("approver"))
    await wait_for_status(client, wf_id, {"completed"})
    assert await _statuses(services, wf_id) == ["running", "waiting_for_approval", "running", "completed"]
    detail = (await client.get(f"/api/workflows/{wf_id}", headers=auth("viewer"))).json()
    assert detail["finished_at"] and detail["duration_ms"] > 0 and detail["current_node"] is None


async def test_cancel_while_waiting_expires_approvals(client, services: Services) -> None:
    repo_id = await connect_demo(client)
    wf_id = await submit(client, repo_id, "Investigate issue #12")
    await wait_for_status(client, wf_id, {"waiting_for_approval"})
    resp = await client.post(f"/api/workflows/{wf_id}/cancel", headers=auth("operator"))
    assert resp.status_code == 200 and resp.json()["status"] == "cancelled"
    async with services.db.session() as s:
        approvals = (await s.execute(select(Approval).where(Approval.workflow_id == wf_id))).scalars().all()
    assert [a.status for a in approvals] == ["expired"]
    # terminal workflows cannot be cancelled again or approved
    assert (await client.post(f"/api/workflows/{wf_id}/cancel", headers=auth("operator"))).status_code == 409
    assert (await client.post(f"/api/approvals/{approvals[0].id}/approve",
                              headers=auth("approver"))).status_code == 409
    assert services.demo_host.write_log == []


async def test_cancel_running_workflow(tmp_path: Path) -> None:
    gate = asyncio.Event()

    def slow_intent(_: str) -> Any:
        return {"intent": "repository_analysis", "confidence": 0.9, "rationale": "r", "search_terms": ["cart"]}

    class Blocking(ScriptedReasoner):
        async def structured(self, **kw: Any):  # noqa: ANN202
            gate.set()
            await asyncio.sleep(30)
            return await super().structured(**kw)

    services = Services(live_settings(tmp_path), reasoner=Blocking({"IntentClassification": slow_intent}))
    await services.startup()
    try:
        app = create_app(services.settings, services)
        app.state.services = services
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
            repo_id = await connect_demo(client)
            wf_id = await submit(client, repo_id, "Describe the architecture please")
            await asyncio.wait_for(gate.wait(), 10)
            resp = await client.post(f"/api/workflows/{wf_id}/cancel", headers=auth("operator"))
            assert resp.json()["status"] == "cancelled"
            assert "running" in await _statuses(services, wf_id)
    finally:
        await services.shutdown()


async def test_step_limit_fails_workflow(client, services: Services) -> None:
    await services.store.update(SettingsUpdate(limits=LimitSettings(
        max_graph_steps=5, workflow_timeout_s=60, node_timeout_s=60, max_debug_revisions=1,
        max_concurrent_workflows=2)), "admin")
    repo_id = await connect_demo(client)
    wf_id = await submit(client, repo_id, "Investigate issue #12")
    wf = await wait_for_status(client, wf_id, {"failed", "waiting_for_approval"})
    assert wf["status"] == "failed"
    assert "step limit" in wf["error"]
    assert wf["result"]["outcome"] == "failed"  # a deterministic report is still produced


async def test_idempotent_submission(client) -> None:
    repo_id = await connect_demo(client)
    a = await submit(client, repo_id, "Investigate issue #12", idempotency_key="ticket-123")
    b = await submit(client, repo_id, "Investigate issue #12", idempotency_key="ticket-123")
    assert a == b
    listing = (await client.get("/api/workflows", headers=auth("viewer"))).json()
    assert listing["total"] == 1


async def test_resume_after_restart(tmp_path: Path) -> None:
    """A workflow paused for approval survives a process restart and resumes from its checkpoint."""
    settings = make_settings(tmp_path)
    first = Services(settings)
    await first.startup()
    app = create_app(settings, first)
    app.state.services = first
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        repo_id = await connect_demo(client)
        wf_id = await submit(client, repo_id, "Investigate issue #12")
        wf = await wait_for_status(client, wf_id, {"waiting_for_approval"})
    await first.shutdown()

    second = Services(settings)  # new process: fresh demo host, same database + checkpoints
    await second.startup()
    try:
        app = create_app(settings, second)
        app.state.services = second
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
            await client.post(f"/api/approvals/{wf['approvals'][0]['id']}/approve", headers=auth("approver"))
            done = await wait_for_status(client, wf_id, {"completed", "failed"})
        assert done["status"] == "completed", done["error"]
        assert [w.kind for w in second.demo_host.write_log] == ["branch", "commit", "pull_request"]
        # nodes before the interrupt were not re-executed after restart
        async with second.db.session() as s:
            starts = (await s.execute(select(WorkflowEvent.node).where(
                WorkflowEvent.workflow_id == wf_id, WorkflowEvent.type == "stage_started"))).scalars().all()
        assert starts.count("debug") == 1
    finally:
        await second.shutdown()


async def test_recovers_interrupted_running_workflow(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    first = Services(settings)
    await first.startup()
    async with first.db.session() as s:
        from orchestra.db.models import Repository

        repo = Repository(provider="demo", full_name="acme/invoicekit")
        s.add(repo)
        await s.flush()
        wf = Workflow(title="t", request="Investigate issue #12", mode="demo", repository_id=repo.id,
                      created_by="operator", status="running")  # as if the process died mid-run
        s.add(wf)
    await first.shutdown()

    second = Services(settings)
    await second.startup()  # recover() resubmits it
    try:
        for _ in range(200):
            async with second.db.session() as s:
                status = (await s.get(Workflow, wf.id)).status
            if status == "waiting_for_approval":
                break
            await asyncio.sleep(0.05)
        assert status == "waiting_for_approval"
    finally:
        await second.shutdown()


# --- live mode with a scripted reasoner ------------------------------------------------


def _live_outputs(fail_first_debug: bool = False) -> dict[str, Any]:
    attempts = {"debug": 0}

    def debug(context: str) -> Any:
        attempts["debug"] += 1
        if fail_first_debug and attempts["debug"] == 1:
            return ReasonerError("Model rate limit reached", transient=True)
        assert "<untrusted_document" in context  # file content is fenced as data
        return {
            "error_signature": "ZeroDivisionError: division by zero",
            "hypotheses": [{"root_cause": "item_count is zero for empty carts", "evidence": ["totals.py:20"],
                            "confidence": 0.9}],
            "proposed_fix": {"summary": "Guard average_item_price for empty carts", "tests_to_add": [],
                             "edits": [{"path": "invoicekit/totals.py",
                                        "find": "    return subtotal(cart) / cart.item_count",
                                        "replace": "    if cart.item_count == 0:\n        return 0.0\n"
                                                   "    return subtotal(cart) / cart.item_count",
                                        "rationale": "avoid division by zero"}]},
            "needs_more_evidence": [],
        }

    return {
        "IntentClassification": lambda _: {"intent": "bug_investigation", "confidence": 0.9, "rationale": "trace",
                                           "issue_number": 12, "search_terms": ["average_item_price", "totals"]},
        "RepositoryContext": lambda _: {
            "summary": "Invoice library", "languages": ["COBOL"], "dependencies": [], "entry_points": [],
            "relevant_files": [{"path": "invoicekit/totals.py", "reason": "trace", "score": 1.0},
                               {"path": "src/hallucinated.py", "reason": "made up", "score": 0.5}],
            "code_relationships": [], "documentation": ["docs/missing.md"], "risks": []},
        "ExecutionPlan": lambda _: {
            "summary": "Fix", "risks": [], "open_questions": [],
            "tasks": [{"id": "T1", "title": "Fix", "description": "", "agent": "debugging", "depends_on": [],
                       "complexity": 2, "acceptance_criteria": ["Empty cart summary does not raise"],
                       "required_tools": [], "required_permissions": []}]},
        "DebugReport": debug,
        # The model tries to approve and to dismiss nothing; static analysis still applies.
        "ReviewReport": lambda _: {"verdict": "approve", "summary": "Looks fine", "findings": [],
                                   "test_coverage": "none"},
        "AcceptanceAssessment": lambda _: {"results": [
            {"name": "empty-cart", "kind": "acceptance", "status": "passed", "required": False,
             "details": "Guard returns 0.0"}]},
        "FinalReport": lambda _: {"headline": "Fixed the empty cart crash", "outcome": "completed",
                                  "summary": "Model summary", "completed": ["invented item"], "pending": [],
                                  "decisions": [], "attention_required": []},
    }


async def _run_live(tmp_path: Path, reasoner: ScriptedReasoner) -> tuple[Services, dict[str, Any], list[dict]]:
    services = Services(live_settings(tmp_path), reasoner=reasoner)
    await services.startup()
    app = create_app(services.settings, services)
    app.state.services = services
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        repo_id = await connect_demo(client)
        wf_id = await submit(client, repo_id, "Investigate issue #12")
        wf = await wait_for_status(client, wf_id, {"waiting_for_approval", "failed"}, timeout=60)
        events = (await client.get(f"/api/workflows/{wf_id}/events", headers=auth("viewer"))).json()
    return services, wf, events


async def test_live_mode_uses_model_outputs_with_guardrails(tmp_path: Path) -> None:
    reasoner = ScriptedReasoner(_live_outputs())
    services, wf, events = await _run_live(tmp_path, reasoner)
    try:
        assert wf["status"] == "waiting_for_approval", wf["error"]
        ctx = wf["state"]["context"]
        assert ctx["languages"] == ["Python"]  # deterministic facts override model claims
        assert [f["path"] for f in ctx["relevant_files"]] == ["invoicekit/totals.py"]  # hallucinated path dropped
        assert ctx["documentation"] == []
        acceptance = [c for c in wf["state"]["verification"]["checks"] if c["kind"] == "acceptance"]
        assert acceptance and all(c["required"] for c in acceptance)  # model can't mark its own checks optional
        outputs = [e for e in events if e["type"] == "agent_output"]
        assert outputs and all(e["payload"]["reasoning"] == "llm" and not e["simulated"] for e in outputs)
        assert {e["payload"]["model"] for e in outputs} == {"scripted-test-model"}
        assert [c[0] for c in reasoner.calls][:4] == ["IntentClassification", "RepositoryContext", "ExecutionPlan",
                                                      "DebugReport"]
    finally:
        await services.shutdown()


async def test_transient_model_failure_is_retried(tmp_path: Path) -> None:
    reasoner = ScriptedReasoner(_live_outputs(fail_first_debug=True))
    services, wf, events = await _run_live(tmp_path, reasoner)
    try:
        assert wf["status"] == "waiting_for_approval", wf["error"]
        retries = [e for e in events if e["type"] == "retry"]
        assert len(retries) == 1 and retries[0]["node"] == "debug"
        assert sum(1 for name, _ in reasoner.calls if name == "DebugReport") == 2
    finally:
        await services.shutdown()


async def test_permanent_model_failure_fails_workflow(tmp_path: Path) -> None:
    outputs = _live_outputs()
    outputs["ExecutionPlan"] = lambda _: ReasonerError("The model declined this request")
    services, wf, _ = await _run_live(tmp_path, ScriptedReasoner(outputs))
    try:
        assert wf["status"] == "failed"
        assert "declined" in wf["error"]
    finally:
        await services.shutdown()


async def test_live_mode_without_key_fails_clearly(tmp_path: Path) -> None:
    services = Services(live_settings(tmp_path))
    await services.startup()
    try:
        app = create_app(services.settings, services)
        app.state.services = services
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
            health = (await client.get("/api/health")).json()
            assert health["status"] == "degraded" and health["checks"]["llm"] == "missing key"
            repo_id = await connect_demo(client)
            wf = await wait_for_status(client, await submit(client, repo_id, "Investigate issue #12"), {"failed"})
            assert "requires a configured model provider" in wf["error"]
    finally:
        await services.shutdown()


@pytest.mark.parametrize("revisions", [0, 1])
async def test_failed_verification_blocks_completion(tmp_path: Path, revisions: int) -> None:
    outputs = _live_outputs()
    bad = outputs["DebugReport"]

    def broken(context: str) -> Any:
        report = bad(context)
        report["proposed_fix"]["edits"][0]["replace"] = "    return (  # unbalanced"
        return report

    outputs["DebugReport"] = broken
    reasoner = ScriptedReasoner(outputs)
    services = Services(live_settings(tmp_path, max_debug_revisions=revisions), reasoner=reasoner)
    await services.startup()
    try:
        app = create_app(services.settings, services)
        app.state.services = services
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
            repo_id = await connect_demo(client)
            wf = await wait_for_status(client, await submit(client, repo_id, "Investigate issue #12"),
                                       {"failed", "waiting_for_approval", "completed"}, timeout=60)
        assert sum(1 for n, _ in reasoner.calls if n == "DebugReport") == revisions + 1
        assert wf["status"] in {"failed", "waiting_for_approval"}
        # A blocked fix never becomes a pull-request proposal; at most an issue comment is proposed.
        assert all(a["action_type"] != "open_pull_request" for a in wf["approvals"])
        assert wf["state"]["verification"]["outcome"] == "failed"
        assert wf["state"]["blocked_reason"] == "required verification checks failed"
        assert any("feedback" in ctx.lower() for name, ctx in reasoner.calls if name == "DebugReport") == (
            revisions > 0)
    finally:
        await services.shutdown()
