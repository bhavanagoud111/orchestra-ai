"""Real sandbox execution. Skipped unless Docker and the sandbox image are available."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from orchestra.demo.host import DemoCodeHost
from orchestra.integrations.sandbox import DockerSandbox
from orchestra.services import Services
from tests.conftest import connect_demo, make_settings, submit, wait_for_status

IMAGE = "orchestra-sandbox-python:3.12"
COMMAND = "python -m pytest -q -p no:cacheprovider"


def _image_available() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True).returncode == 0  # noqa: S603,S607


pytestmark = pytest.mark.skipif(not _image_available(), reason=f"docker image {IMAGE} not available")

REGRESSION_TEST = '''from invoicekit.cart import Cart
from invoicekit.totals import invoice_summary


def test_empty_cart_summary():
    assert invoice_summary(Cart())["total"] == 0
'''


async def test_sandbox_runs_tests_without_network() -> None:
    sandbox = DockerSandbox(IMAGE, timeout_s=120)
    files = dict(DemoCodeHost().files)
    files["tests/test_regression.py"] = REGRESSION_TEST

    before = await sandbox.run(files, COMMAND)
    assert before.status == "failed" and "ZeroDivisionError" in before.stdout_tail

    fixed = files["invoicekit/totals.py"].replace(
        "    return subtotal(cart) / cart.item_count",
        "    if not cart.item_count:\n        return 0.0\n    return subtotal(cart) / cart.item_count")
    after = await sandbox.run({**files, "invoicekit/totals.py": fixed}, COMMAND)
    assert after.status == "passed", after.stdout_tail
    assert "4 passed" in after.stdout_tail

    probe = await sandbox.run({"x.py": ""}, "python -c \"import socket; socket.create_connection(('1.1.1.1', 53), 3)\"")
    assert probe.status == "failed"
    write = await sandbox.run({"x.py": ""}, "touch /workspace/new_file")
    assert write.status == "failed"  # workspace is mounted read-only


async def test_verification_runs_tests_in_sandbox(tmp_path: Path) -> None:
    import httpx

    from orchestra.api.app import create_app

    settings = make_settings(tmp_path, sandbox_enabled=True, sandbox_image=IMAGE, sandbox_command=COMMAND)
    services = Services(settings)
    await services.startup()
    try:
        app = create_app(settings, services)
        app.state.services = services
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
            repo_id = await connect_demo(client)
            wf = await wait_for_status(client, await submit(client, repo_id, "Investigate issue #12"),
                                       {"waiting_for_approval", "failed"}, wait_s=180)
        checks = {c["name"]: c for c in wf["state"]["verification"]["checks"]}
        assert checks["tests"]["status"] == "passed", checks["tests"]["details"]
        assert wf["state"]["verification"]["outcome"] == "passed"
        assert "Tests were not executed in the sandbox" not in wf["approvals"][0]["risk_reasons"]
    finally:
        await services.shutdown()
