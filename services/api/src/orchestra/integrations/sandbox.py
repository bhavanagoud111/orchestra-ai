"""Isolated test execution in a throwaway Docker container.

The container gets: no network, a read-only root filesystem and read-only
workspace mount, a small tmpfs, all capabilities dropped, no-new-privileges,
an unprivileged user, CPU/memory/PID limits, and a wall-clock timeout. No host
credentials, sockets, or home directories are mounted. Code is never executed
on the host itself.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import uuid
from pathlib import Path, PurePosixPath

from pydantic import BaseModel


class SandboxResult(BaseModel):
    status: str  # passed | failed | timeout | unavailable | error
    exit_code: int | None
    stdout_tail: str
    duration_s: float
    command: str


class SandboxUnavailable(RuntimeError):
    pass


def _safe_relpath(path: str) -> PurePosixPath:
    p = PurePosixPath(path)
    if p.is_absolute() or ".." in p.parts:
        raise ValueError(f"Unsafe path in sandbox snapshot: {path}")
    return p


def _materialize(root: Path, files: dict[str, str]) -> None:
    for path, content in files.items():
        target = root / _safe_relpath(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    root.chmod(0o755)
    for p in root.rglob("*"):
        p.chmod(0o755 if p.is_dir() else 0o644)


class DockerSandbox:
    def __init__(
        self, image: str, *, cpus: float = 1.0, memory: str = "512m", timeout_s: int = 300, docker_bin: str = "docker"
    ) -> None:
        self.image = image
        self.cpus = cpus
        self.memory = memory
        self.timeout_s = timeout_s
        self.docker_bin = docker_bin

    def available(self) -> bool:
        return shutil.which(self.docker_bin) is not None

    def build_command(self, workdir: Path, command: str, name: str) -> list[str]:
        return [
            self.docker_bin,
            "run",
            "--rm",
            "--name",
            name,
            "--network",
            "none",
            "--cpus",
            str(self.cpus),
            "--memory",
            self.memory,
            "--memory-swap",
            self.memory,
            "--pids-limit",
            "256",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",  # noqa: S108 - container-internal tmpfs
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "65534:65534",
            "-e",
            "PYTHONDONTWRITEBYTECODE=1",
            "-e",
            "HOME=/tmp",
            "-v",
            f"{workdir}:/workspace:ro",
            "-w",
            "/workspace",
            self.image,
            "sh",
            "-c",
            command,
        ]

    async def run(self, files: dict[str, str], command: str) -> SandboxResult:
        if not self.available():
            raise SandboxUnavailable("docker executable not found")
        name = f"orchestra-sbx-{uuid.uuid4().hex[:12]}"
        loop = asyncio.get_running_loop()
        started = loop.time()
        with tempfile.TemporaryDirectory(prefix="orchestra-sbx-") as tmp:
            root = Path(tmp)
            await asyncio.to_thread(_materialize, root, files)
            proc = await asyncio.create_subprocess_exec(
                *self.build_command(root, command, name),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=self.timeout_s)
            except TimeoutError:
                kill = await asyncio.create_subprocess_exec(
                    self.docker_bin, "kill", name, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
                )
                await kill.wait()
                proc.kill()
                return SandboxResult(
                    status="timeout", exit_code=None, stdout_tail="", duration_s=loop.time() - started, command=command
                )
        text = out.decode("utf-8", errors="replace")
        code = proc.returncode
        status = "passed" if code == 0 else ("error" if code in (125, 126, 127) else "failed")
        return SandboxResult(
            status=status,
            exit_code=code,
            stdout_tail=text[-6000:],
            duration_s=round(loop.time() - started, 2),
            command=command,
        )
