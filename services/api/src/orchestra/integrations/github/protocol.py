"""Code-host interface shared by the live GitHub client and the demo host.

Write operations are deliberately limited to: creating branches under the
``orchestra/`` prefix, committing files to such branches, opening *draft* pull
requests, creating issues, and commenting. There is no merge, force-push,
delete, or settings operation anywhere in this interface.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

BRANCH_PREFIX = "orchestra/"


class RepoInfo(BaseModel):
    full_name: str
    description: str | None = None
    default_branch: str = "main"
    visibility: str = "public"
    html_url: str | None = None
    language: str | None = None
    stars: int = 0
    open_issues: int = 0
    pushed_at: str | None = None


class TreeEntry(BaseModel):
    path: str
    type: str  # "blob" | "tree"
    size: int | None = None


class IssueInfo(BaseModel):
    number: int
    title: str
    body: str | None = None
    state: str
    labels: list[str] = []
    author: str | None = None
    html_url: str | None = None
    created_at: str | None = None
    is_pull_request: bool = False


class PullInfo(BaseModel):
    number: int
    title: str
    body: str | None = None
    state: str
    head: str
    base: str
    draft: bool = False
    author: str | None = None
    html_url: str | None = None
    created_at: str | None = None


class FileDiff(BaseModel):
    filename: str
    status: str
    additions: int = 0
    deletions: int = 0
    patch: str | None = None


class CheckRun(BaseModel):
    name: str
    status: str
    conclusion: str | None = None
    html_url: str | None = None


class CIStatus(BaseModel):
    ref: str
    state: str  # success | failure | pending | none
    checks: list[CheckRun] = []


class WriteResult(BaseModel):
    kind: str
    url: str | None
    number: int | None = None
    ref: str | None = None
    simulated: bool = False
    reused: bool = False


class CodeHost(Protocol):
    simulated: bool

    async def get_repo(self, full_name: str) -> RepoInfo: ...
    async def list_repos(self, page: int = 1, per_page: int = 30) -> list[RepoInfo]: ...
    async def get_tree(self, full_name: str, ref: str) -> list[TreeEntry]: ...
    async def read_file(self, full_name: str, path: str, ref: str) -> str: ...
    async def list_issues(self, full_name: str, state: str = "open", limit: int = 30) -> list[IssueInfo]: ...
    async def get_issue(self, full_name: str, number: int) -> IssueInfo: ...
    async def list_pulls(self, full_name: str, state: str = "open", limit: int = 30) -> list[PullInfo]: ...
    async def get_pull(self, full_name: str, number: int) -> PullInfo: ...
    async def get_pull_files(self, full_name: str, number: int) -> list[FileDiff]: ...
    async def get_ci_status(self, full_name: str, ref: str) -> CIStatus: ...

    async def create_branch(self, full_name: str, branch: str, from_ref: str) -> WriteResult: ...
    async def commit_files(self, full_name: str, branch: str, files: dict[str, str], message: str) -> WriteResult: ...
    async def open_draft_pull(self, full_name: str, head: str, base: str, title: str, body: str) -> WriteResult: ...
    async def create_issue(self, full_name: str, title: str, body: str, labels: list[str]) -> WriteResult: ...
    async def comment(self, full_name: str, number: int, body: str) -> WriteResult: ...


class CodeHostError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, transient: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.transient = transient


def assert_safe_branch(branch: str, default_branch: str) -> None:
    if not branch.startswith(BRANCH_PREFIX):
        raise CodeHostError(f"Refusing to write to branch {branch!r}: must start with {BRANCH_PREFIX!r}")
    if branch == default_branch or ".." in branch or branch.endswith(".lock"):
        raise CodeHostError(f"Refusing to write to branch {branch!r}")
