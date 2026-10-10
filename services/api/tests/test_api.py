"""API authentication, authorization, validation, pagination, settings, and middleware."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

from orchestra.api.app import create_app
from orchestra.services import Services
from tests.conftest import TOKENS, auth, connect_demo, make_settings, submit, wait_for_status


async def test_health_is_public_everything_else_requires_a_token(client) -> None:
    health = await client.get("/api/health")
    assert health.status_code == 200 and health.json()["mode"] == "demo"
    for path in ("/api/workflows", "/api/agents", "/api/repositories", "/api/approvals", "/api/settings",
                 "/api/metrics", "/api/tools"):
        assert (await client.get(path)).status_code == 401, path
        assert (await client.get(path, headers={"Authorization": "Bearer wrong-token"})).status_code == 401


@pytest.mark.parametrize(
    ("role", "allowed"),
    [("viewer", set()), ("operator", {"create", "connect"}), ("approver", {"create", "connect", "approve"}),
     ("admin", {"create", "connect", "approve", "settings"})],
)
async def test_role_based_authorization(client, role: str, allowed: set[str]) -> None:
    repo_id = await connect_demo(client)
    wf_id = await submit(client, repo_id, "Investigate issue #12")
    wf = await wait_for_status(client, wf_id, {"waiting_for_approval"})
    h = auth(role)
    results = {
        "connect": (await client.post("/api/repositories/connect", json={"provider": "demo",
                                                                        "full_name": "acme/invoicekit"},
                                      headers=h)).status_code,
        "create": (await client.post("/api/workflows", json={"request": "Investigate issue #12",
                                                             "repository_id": repo_id}, headers=h)).status_code,
        "settings": (await client.put("/api/settings", json={}, headers=h)).status_code,
        "approve": (await client.post(f"/api/approvals/{wf['approvals'][0]['id']}/approve",
                                      headers=h)).status_code,
    }
    for action, status in results.items():
        if action in allowed:
            assert status in (200, 201, 202), (action, status)
        else:
            assert status == 403, (action, status)
    assert (await client.get("/api/me", headers=h)).json()["role"] == role


async def test_validation_errors_and_not_found(client) -> None:
    resp = await client.post("/api/workflows", json={"request": "hi", "repository_id": "x"}, headers=auth("operator"))
    assert resp.status_code == 422 and resp.json()["error"] == "validation_error"
    resp = await client.post("/api/workflows", json={"request": "Investigate a bug", "repository_id": "nope"},
                             headers=auth("operator"))
    assert resp.status_code == 404
    assert (await client.get("/api/workflows/missing", headers=auth("viewer"))).status_code == 404
    bad = await client.post("/api/repositories/connect", json={"provider": "demo", "full_name": "../../etc"},
                            headers=auth("operator"))
    assert bad.status_code == 422
    other = await client.post("/api/repositories/connect", json={"provider": "demo", "full_name": "a/b"},
                              headers=auth("operator"))
    assert other.status_code == 422
    gh = await client.post("/api/repositories/connect", json={"provider": "github", "full_name": "a/b"},
                           headers=auth("operator"))
    assert gh.status_code == 409 and "not connected" in gh.json()["detail"]


async def test_pagination(client) -> None:
    repo_id = await connect_demo(client)
    for i in range(3):
        await submit(client, repo_id, f"Give me an architecture overview number {i}")
    page = (await client.get("/api/workflows?limit=2&offset=0", headers=auth("viewer"))).json()
    assert page["total"] == 3 and len(page["items"]) == 2 and page["limit"] == 2
    rest = (await client.get("/api/workflows?limit=2&offset=2", headers=auth("viewer"))).json()
    assert len(rest["items"]) == 1
    assert (await client.get("/api/workflows?limit=500", headers=auth("viewer"))).status_code == 422


async def test_settings_never_return_secrets_and_store_them_encrypted(client, services: Services) -> None:
    secret = "sk-ant-api03-supersecretvalue0123456789abcdef"
    resp = await client.put("/api/settings", json={"secrets": {"anthropic_api_key": secret},
                                                   "tools": {"disabled": ["repo.tree"]}}, headers=auth("admin"))
    assert resp.status_code == 200
    assert secret not in resp.text
    body = (await client.get("/api/settings", headers=auth("viewer"))).json()
    assert body["secrets"]["anthropic_api_key"] == {"configured": True, "source": "settings"}
    assert body["effective"]["tools"]["disabled"] == ["repo.tree"]
    assert secret not in str(body)
    async with services.db.session() as s:
        raw = (await s.execute(text("SELECT secret FROM settings WHERE key='anthropic_api_key'"))).scalar_one()
    assert secret.encode() not in raw
    assert services.store.secret("anthropic_api_key") == secret
    bad = await client.put("/api/settings", json={"tools": {"disabled": ["no.such.tool"]}}, headers=auth("admin"))
    assert bad.status_code == 422


async def test_agents_tools_metrics_and_graph(client) -> None:
    repo_id = await connect_demo(client)
    wf_id = await submit(client, repo_id, "Investigate issue #12")
    await wait_for_status(client, wf_id, {"waiting_for_approval"})
    agents = (await client.get("/api/agents", headers=auth("viewer"))).json()
    assert len(agents) == 8
    by_name = {a["name"]: a for a in agents}
    assert by_name["repository_intelligence"]["tool_calls_24h"] > 0
    assert by_name["debugging"]["last_execution"]["simulated"] is True
    assert by_name["automation"]["tools"] == sorted(by_name["automation"]["tools"])
    tools = (await client.get("/api/tools", headers=auth("viewer"))).json()
    writes = {t["name"] for t in tools["tools"] if t["permission"] == "write"}
    assert writes == {"github.create_branch", "github.commit_files", "github.open_draft_pull", "github.create_issue",
                      "github.comment"}
    metrics = (await client.get("/api/metrics", headers=auth("viewer"))).json()
    assert metrics["pending_approvals"] == 1 and metrics["connected_repositories"] == 1
    assert len(metrics["executions_last_7d"]) == 7
    graph = (await client.get("/api/workflow-graph", headers=auth("viewer"))).json()
    assert "approval_gate" in {n["id"] for n in graph["nodes"]}


async def test_repository_browsing(client) -> None:
    repo_id = await connect_demo(client)
    tree = (await client.get(f"/api/repositories/{repo_id}/tree", headers=auth("viewer"))).json()
    assert "invoicekit/totals.py" in tree["files"] and tree["languages"] == {"Python": 6}
    f = (await client.get(f"/api/repositories/{repo_id}/files", params={"path": "invoicekit/cart.py"},
                          headers=auth("viewer"))).json()
    assert "class Cart" in f["content"]
    issues = (await client.get(f"/api/repositories/{repo_id}/issues", headers=auth("viewer"))).json()
    assert {i["number"] for i in issues} == {12, 14, 15}
    pulls = (await client.get(f"/api/repositories/{repo_id}/pulls", headers=auth("viewer"))).json()
    assert pulls[0]["number"] == 9
    ci = (await client.get(f"/api/repositories/{repo_id}/ci", headers=auth("viewer"))).json()
    assert ci["state"] == "success"


async def test_request_id_and_openapi(client) -> None:
    resp = await client.get("/api/health", headers={"X-Request-ID": "trace-abc-123"})
    assert resp.headers["x-request-id"] == "trace-abc-123"
    generated = (await client.get("/api/health", headers={"X-Request-ID": "bad id!"})).headers["x-request-id"]
    assert generated != "bad id!" and len(generated) == 32
    spec = (await client.get("/openapi.json")).json()
    for path in ("/api/workflows", "/api/workflows/{workflow_id}/cancel", "/api/workflows/{workflow_id}/events",
                 "/api/agents", "/api/repositories", "/api/repositories/connect",
                 "/api/repositories/{repository_id}/analyze", "/api/approvals",
                 "/api/approvals/{approval_id}/approve", "/api/approvals/{approval_id}/reject", "/api/health",
                 "/api/metrics"):
        assert path in spec["paths"], path


async def test_rate_limiting(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, rate_limit_per_minute=3)
    services = Services(settings)
    await services.startup()
    try:
        app = create_app(settings, services)
        app.state.services = services
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
            codes = [(await client.get("/api/me", headers=auth("viewer"))).status_code for _ in range(4)]
            assert codes == [200, 200, 200, 429]
            assert (await client.get("/api/health")).status_code == 200  # exempt
            # limits are per credential
            assert (await client.get("/api/me", headers=auth("admin"))).status_code == 200
    finally:
        await services.shutdown()


def test_app_refuses_to_start_without_tokens(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, admin_token=None, approver_token=None, operator_token=None, viewer_token=None)
    with pytest.raises(RuntimeError, match="API token"):
        create_app(settings, Services(settings))


def test_tokens_are_distinct() -> None:
    assert len(set(TOKENS.values())) == 4


@pytest.mark.parametrize(("raw", "expected"), [
    ("http://localhost:3000", ["http://localhost:3000"]),
    ("http://a.test, http://b.test", ["http://a.test", "http://b.test"]),
    ('["http://c.test"]', ["http://c.test"]),
])
def test_cors_origins_from_environment(monkeypatch, raw: str, expected: list[str]) -> None:
    from orchestra.config import Settings

    monkeypatch.setenv("ORCHESTRA_CORS_ORIGINS", raw)
    assert Settings(_env_file=None).cors_origins == expected  # type: ignore[call-arg]
