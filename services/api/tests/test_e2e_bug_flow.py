"""End-to-end scenario through the HTTP API (demo mode, simulated GitHub).

1. connect a sample repository           6. Verification evaluates the proposal
2. submit a bug investigation request    7. approval is requested before GitHub writes
3. Repository Intelligence ranks files   8. approval creates a draft pull request
4. Planning produces a plan              9. the timeline records the outcome
5. Debugging proposes a fix
"""

from __future__ import annotations

import json

from orchestra.services import Services
from tests.conftest import auth, connect_demo, submit, wait_for_status


async def test_bug_investigation_to_draft_pull_request(client, services: Services) -> None:
    repo_id = await connect_demo(client)
    wf_id = await submit(client, repo_id, "Checkout fails for empty carts, see issue #12. Please investigate.")

    wf = await wait_for_status(client, wf_id, {"waiting_for_approval", "failed"})
    assert wf["status"] == "waiting_for_approval", wf["error"]
    state = wf["state"]

    assert state["intent"]["intent"] == "bug_investigation"
    assert state["intent"]["issue_number"] == 12
    ranked = [f["path"] for f in state["context"]["relevant_files"]]
    assert "invoicekit/totals.py" in ranked[:3]

    tasks = state["plan"]["tasks"]
    assert [t["agent"] for t in tasks][:2] == ["repository_intelligence", "debugging"]
    assert all(set(t["depends_on"]) <= {x["id"] for x in tasks} for t in tasks)

    fix = state["debug"]["proposed_fix"]
    assert fix["edits"][0]["path"] == "invoicekit/totals.py"
    assert "if not cart.item_count:" in state["changed_files"]["invoicekit/totals.py"]

    checks = {c["name"]: c for c in state["verification"]["checks"]}
    assert checks["patch-applies"]["status"] == "passed"
    assert checks["syntax:invoicekit/totals.py"]["status"] == "passed"
    assert checks["review-gate"]["status"] == "passed"
    assert checks["tests"]["status"] == "skipped"  # sandbox disabled in this test
    assert state["verification"]["outcome"] == "unverified"

    # 7. nothing has been written yet; one approval is pending with a diff and risk assessment
    assert services.demo_host.write_log == []
    [approval] = wf["approvals"]
    assert approval["status"] == "pending"
    assert approval["action_type"] == "open_pull_request"
    assert approval["simulated"] is True
    assert approval["risk_level"] == "medium"
    assert "Tests were not executed in the sandbox" in approval["risk_reasons"]
    diff = approval["proposed_changes"]["diffs"]["invoicekit/totals.py"]
    assert "+    if not cart.item_count:" in diff

    pending = (await client.get("/api/approvals?status=pending", headers=auth("viewer"))).json()
    assert [a["id"] for a in pending["items"]] == [approval["id"]]

    # 8. approve -> branch, commit, draft PR
    resp = await client.post(f"/api/approvals/{approval['id']}/approve", json={"reason": "LGTM"},
                             headers=auth("approver"))
    assert resp.status_code == 200, resp.text
    assert resp.json()["decided_by"] == "approver"

    wf = await wait_for_status(client, wf_id, {"completed", "failed"})
    assert wf["status"] == "completed", wf["error"]
    kinds = [w.kind for w in services.demo_host.write_log]
    assert kinds == ["branch", "commit", "pull_request"]
    pr = services.demo_host.pulls[-1]
    assert pr.draft and pr.head.startswith("orchestra/fix-12-") and pr.base == "main"
    assert "Refs #12" in (pr.body or "")
    assert services.demo_host.branches[pr.head]["invoicekit/totals.py"].count("if not cart.item_count:") == 1
    assert "if not cart.item_count" not in services.demo_host.branches["main"]["invoicekit/totals.py"]

    report = wf["result"]
    assert any("pull/" in line and "(simulated)" in line for line in report["completed"])
    assert wf["state"]["action_results"][0]["status"] == "succeeded"

    # 9. timeline records the full sequence
    events = (await client.get(f"/api/workflows/{wf_id}/events", headers=auth("viewer"))).json()
    types = [e["type"] for e in events]
    assert [e["seq"] for e in events] == sorted(e["seq"] for e in events)
    for expected in ("routing_decision", "approval_requested", "approval_decided", "tool_call", "tool_result"):
        assert expected in types
    nodes_started = [e["node"] for e in events if e["type"] == "stage_started"]
    assert nodes_started == ["classify", "retrieve_context", "plan", "dispatch", "debug", "review", "verify",
                             "propose_actions", "approval_gate", "approval_gate", "execute_actions", "report"]
    assert all(e["simulated"] for e in events if e["type"] == "agent_output")

    calls = (await client.get(f"/api/workflows/{wf_id}/tool-calls", headers=auth("viewer"))).json()
    writes = [c for c in calls if c["permission"] == "write"]
    assert [c["tool"] for c in writes] == ["github.create_branch", "github.commit_files", "github.open_draft_pull"]
    assert all(c["approval_id"] == approval["id"] and c["status"] == "succeeded" for c in writes)

    # SSE replay of a finished workflow ends with an end event
    async with client.stream("GET", f"/api/workflows/{wf_id}/events?stream=true", headers=auth("viewer")) as resp:
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = ""
        async for chunk in resp.aiter_text():
            body += chunk
            if "event: end" in body:
                break
    data_lines = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: {\"id\"")]
    assert len(data_lines) == len(events)

    # memory captured the investigation for future workflows
    hits = await services.memory.recall("ZeroDivisionError empty cart", repository_id=repo_id)
    assert any("division" in h.content.lower() for h in hits)


async def test_rejected_action_is_not_executed(client, services: Services) -> None:
    repo_id = await connect_demo(client)
    wf_id = await submit(client, repo_id, "Investigate issue #12")
    wf = await wait_for_status(client, wf_id, {"waiting_for_approval"})
    approval_id = wf["approvals"][0]["id"]

    resp = await client.post(f"/api/approvals/{approval_id}/reject", json={"reason": "Prefer a different fix"},
                             headers=auth("approver"))
    assert resp.status_code == 200
    wf = await wait_for_status(client, wf_id, {"completed", "failed"})
    assert wf["status"] == "completed"
    assert services.demo_host.write_log == []
    assert wf["state"]["action_results"] == [
        {"title": wf["approvals"][0]["title"], "status": "skipped", "reason": "rejected"}]
    assert any("rejected" in p for p in wf["result"]["pending"])

    # a decided approval cannot be decided again
    again = await client.post(f"/api/approvals/{approval_id}/approve", headers=auth("approver"))
    assert again.status_code == 409


async def test_prompt_injection_is_flagged_and_escalates_risk(client, services: Services) -> None:
    repo_id = await connect_demo(client)
    wf_id = await submit(client, repo_id, "The build is broken, see issue #15")
    wf = await wait_for_status(client, wf_id, {"waiting_for_approval", "completed"})
    events = (await client.get(f"/api/workflows/{wf_id}/events", headers=auth("viewer"))).json()
    warnings = [e for e in events if e["type"] == "security_warning"]
    assert warnings and "issue #15" in warnings[0]["message"]
    rules = {f["rule"] for f in warnings[0]["payload"]["findings"]}
    assert {"override_instructions", "approval_bypass", "credential_exfiltration"} <= rules
    # The injected instructions did not cause any merge or write; the only proposal needs approval at high risk.
    assert services.demo_host.write_log == []
    assert all(a["risk_level"] == "high" for a in wf["approvals"])
    assert all(a["action_type"] != "merge" for a in wf["approvals"])


async def test_feature_planning_creates_tracking_issue(client, services: Services) -> None:
    repo_id = await connect_demo(client)
    wf_id = await submit(client, repo_id, "Plan the work for issue #14")
    wf = await wait_for_status(client, wf_id, {"waiting_for_approval"})
    assert wf["state"]["intent"]["intent"] == "feature_planning"
    [approval] = wf["approvals"]
    assert approval["action_type"] == "create_issue" and approval["risk_level"] == "low"
    await client.post(f"/api/approvals/{approval['id']}/approve", headers=auth("approver"))
    wf = await wait_for_status(client, wf_id, {"completed"})
    issue = services.demo_host.issues[-1]
    assert issue.title.startswith("Tracking:") and "- [ ] **T1" in (issue.body or "")


async def test_code_review_of_pull_request(client, services: Services) -> None:
    repo_id = await connect_demo(client)
    wf_id = await submit(client, repo_id, "Review PR #9 before we merge it")
    wf = await wait_for_status(client, wf_id, {"waiting_for_approval"})
    review = wf["state"]["review"]
    assert review["verdict"] == "request_changes"
    messages = " ".join(f["message"] for f in review["findings"])
    assert "timeout" in messages and "Plain-HTTP" in messages
    assert wf["approvals"][0]["action_type"] == "comment_on_issue"


async def test_repository_analysis_updates_repository(client) -> None:
    repo_id = await connect_demo(client)
    resp = await client.post(f"/api/repositories/{repo_id}/analyze", headers=auth("operator"))
    assert resp.status_code == 202
    wf = await wait_for_status(client, resp.json()["id"], {"completed", "failed"})
    assert wf["status"] == "completed" and wf["approvals"] == []
    repo = (await client.get(f"/api/repositories/{repo_id}", headers=auth("viewer"))).json()
    assert repo["analysis"]["languages"] == ["Python"]
    assert repo["last_analyzed_at"] is not None
