from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from orchestra.agents.reasoner import Usage
from orchestra.api.app import create_app
from orchestra.config import RunMode, Settings
from orchestra.security.crypto import generate_key
from orchestra.services import Services

TOKENS = {
    "admin": "admin-token-0123456789",
    "approver": "approver-token-0123456789",
    "operator": "operator-token-0123456789",
    "viewer": "viewer-token-0123456789",
}
TERMINAL = {"completed", "failed", "cancelled"}


def make_settings(tmp: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = dict(
        data_dir=tmp,
        database_url=f"sqlite+aiosqlite:///{tmp}/orchestra.db",
        admin_token=TOKENS["admin"],
        approver_token=TOKENS["approver"],
        operator_token=TOKENS["operator"],
        viewer_token=TOKENS["viewer"],
        secret_key=generate_key(),
        rate_limit_per_minute=0,
        mcp_config_path=None,
        sandbox_enabled=False,
    )
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
async def services(settings: Settings) -> AsyncIterator[Services]:
    svc = Services(settings)
    await svc.startup()
    yield svc
    await svc.shutdown()


def auth(role: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[role]}"}


@pytest.fixture
async def client(services: Services) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(services.settings, services)
    app.state.services = services
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def wait_for_status(client: httpx.AsyncClient, workflow_id: str, statuses: set[str],
                          timeout: float = 30.0) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        resp = await client.get(f"/api/workflows/{workflow_id}", headers=auth("viewer"))
        body = resp.json()
        if body["status"] in statuses:
            return body
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"workflow stuck in {body['status']}: {body.get('error')}")
        await asyncio.sleep(0.05)


async def connect_demo(client: httpx.AsyncClient) -> str:
    resp = await client.post("/api/repositories/connect", json={"provider": "demo", "full_name": "acme/invoicekit"},
                             headers=auth("operator"))
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def submit(client: httpx.AsyncClient, repo_id: str, request: str, **extra: Any) -> str:
    resp = await client.post("/api/workflows", json={"request": request, "repository_id": repo_id, **extra},
                             headers=auth("operator"))
    assert resp.status_code == 202, resp.text
    return resp.json()["id"]


class ScriptedReasoner:
    """Stand-in for the LLM in live-mode tests: returns canned outputs keyed by schema name."""

    model = "scripted-test-model"

    def __init__(self, outputs: dict[str, Callable[[str], Any]]) -> None:
        self.outputs = outputs
        self.calls: list[tuple[str, str]] = []

    async def structured(self, *, agent: str, instructions: str, context: str, schema: type) -> tuple[Any, Usage]:
        self.calls.append((schema.__name__, context))
        produce = self.outputs[schema.__name__]
        value = produce(context)
        if isinstance(value, Exception):
            raise value
        return schema.model_validate(value), Usage(input_tokens=len(context) // 4, output_tokens=200,
                                                   model=self.model, duration_ms=1.0)


def live_settings(tmp: Path, **overrides: Any) -> Settings:
    return make_settings(tmp, mode=RunMode.LIVE, **overrides)
