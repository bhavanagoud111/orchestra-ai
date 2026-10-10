"""Built-in tools available to agents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from orchestra.integrations.github.protocol import (
    BRANCH_PREFIX,
    CIStatus,
    CodeHost,
    FileDiff,
    IssueInfo,
    PullInfo,
    WriteResult,
)
from orchestra.integrations.sandbox import DockerSandbox, SandboxResult, SandboxUnavailable
from orchestra.memory.store import MemoryKind, MemoryStore
from orchestra.retrieval import RepositoryReader
from orchestra.tools.executor import ToolError
from orchestra.tools.registry import Permission, ToolRegistry, ToolSpec


@dataclass
class ToolRuntime:
    """Per-workflow resources handed to tool handlers. Never exposed to models."""

    host: CodeHost
    full_name: str
    default_branch: str
    reader: RepositoryReader
    memory: MemoryStore
    repository_id: str | None
    sandbox: DockerSandbox | None
    sandbox_command: str


# --- input models ------------------------------------------------------------------


class NoArgs(BaseModel):
    pass


class SearchArgs(BaseModel):
    terms: list[str] = Field(min_length=1, max_length=40)
    priority_paths: list[str] = Field(default_factory=list, max_length=20)
    top_k: int = Field(default=8, ge=1, le=20)


class ReadFileArgs(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)


class NumberArgs(BaseModel):
    number: int = Field(ge=1)


class ListIssuesArgs(BaseModel):
    state: str = Field(default="open", pattern="^(open|closed|all)$")
    limit: int = Field(default=20, ge=1, le=100)


class RefArgs(BaseModel):
    ref: str = Field(min_length=1, max_length=255)


class RecallArgs(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    kinds: list[MemoryKind] = Field(default_factory=list)
    limit: int = Field(default=5, ge=1, le=20)


class RunTestsArgs(BaseModel):
    files: dict[str, str] = Field(description="Full repository snapshot with proposed edits applied")


class BranchArgs(BaseModel):
    branch: str = Field(pattern=rf"^{BRANCH_PREFIX}[A-Za-z0-9._/-]{{1,80}}$")


class CommitArgs(BaseModel):
    branch: str = Field(pattern=rf"^{BRANCH_PREFIX}[A-Za-z0-9._/-]{{1,80}}$")
    files: dict[str, str] = Field(min_length=1, max_length=50)
    message: str = Field(min_length=1, max_length=2000)


class PullArgs(BaseModel):
    head: str = Field(pattern=rf"^{BRANCH_PREFIX}[A-Za-z0-9._/-]{{1,80}}$")
    title: str = Field(min_length=1, max_length=250)
    body: str = Field(max_length=60_000)


class IssueArgs(BaseModel):
    title: str = Field(min_length=1, max_length=250)
    body: str = Field(max_length=60_000)
    labels: list[str] = Field(default_factory=list, max_length=10)


class CommentArgs(BaseModel):
    number: int = Field(ge=1)
    body: str = Field(min_length=1, max_length=60_000)


# --- output models -----------------------------------------------------------------


class TreeResult(BaseModel):
    files: list[str]
    truncated: bool


class SearchHit(BaseModel):
    path: str
    start_line: int
    end_line: int
    score: float
    text: str


class SearchResult(BaseModel):
    hits: list[SearchHit]
    skipped: list[str]


class FileResult(BaseModel):
    path: str
    content: str | None
    start_line: int
    end_line: int
    total_lines: int
    skipped_reason: str | None = None


class IssueList(BaseModel):
    issues: list[IssueInfo]


class PullDetail(BaseModel):
    pull: PullInfo
    files: list[FileDiff] = []


class RecallHit(BaseModel):
    kind: str
    content: str
    score: float
    created_at: str


class RecallResult(BaseModel):
    hits: list[RecallHit]


# --- handlers ----------------------------------------------------------------------


async def _tree(_: NoArgs, rt: ToolRuntime) -> TreeResult:
    paths = [e.path for e in await rt.reader.tree()]
    return TreeResult(files=paths[:2000], truncated=len(paths) > 2000)


async def _search(args: SearchArgs, rt: ToolRuntime) -> SearchResult:
    chunks = await rt.reader.search(args.terms, top_k=args.top_k, priority_paths=args.priority_paths)
    return SearchResult(
        hits=[
            SearchHit(path=c.path, start_line=c.start_line, end_line=c.end_line, score=round(c.score, 3), text=c.text)
            for c in chunks
        ],
        skipped=rt.reader.skipped[-20:],
    )


async def _read_file(args: ReadFileArgs, rt: ToolRuntime) -> FileResult:
    content = await rt.reader.read(args.path)
    if content is None:
        reason = next((s for s in rt.reader.skipped if s.startswith(args.path)), "not found")
        return FileResult(path=args.path, content=None, start_line=0, end_line=0, total_lines=0, skipped_reason=reason)
    lines = content.splitlines()
    start = args.start_line or 1
    end = min(args.end_line or len(lines), len(lines), start + 400)
    return FileResult(
        path=args.path,
        content="\n".join(lines[start - 1 : end]),
        start_line=start,
        end_line=end,
        total_lines=len(lines),
    )


async def _list_issues(args: ListIssuesArgs, rt: ToolRuntime) -> IssueList:
    return IssueList(issues=await rt.host.list_issues(rt.full_name, args.state, args.limit))


async def _get_issue(args: NumberArgs, rt: ToolRuntime) -> IssueInfo:
    return await rt.host.get_issue(rt.full_name, args.number)


async def _get_pull(args: NumberArgs, rt: ToolRuntime) -> PullDetail:
    return PullDetail(pull=await rt.host.get_pull(rt.full_name, args.number))


async def _get_pull_files(args: NumberArgs, rt: ToolRuntime) -> PullDetail:
    pull = await rt.host.get_pull(rt.full_name, args.number)
    return PullDetail(pull=pull, files=await rt.host.get_pull_files(rt.full_name, args.number))


async def _ci_status(args: RefArgs, rt: ToolRuntime) -> CIStatus:
    return await rt.host.get_ci_status(rt.full_name, args.ref)


async def _recall(args: RecallArgs, rt: ToolRuntime) -> RecallResult:
    hits = await rt.memory.recall(
        args.query, kinds=args.kinds or None, repository_id=rt.repository_id, limit=args.limit
    )
    return RecallResult(
        hits=[RecallHit(kind=h.kind, content=h.content, score=round(h.score, 3), created_at=h.created_at) for h in hits]
    )


async def _run_tests(args: RunTestsArgs, rt: ToolRuntime) -> SandboxResult:
    if rt.sandbox is None:
        raise ToolError("Sandbox execution is disabled (ORCHESTRA_SANDBOX_ENABLED=false)")
    try:
        return await rt.sandbox.run(args.files, rt.sandbox_command)
    except SandboxUnavailable as exc:
        raise ToolError(f"Sandbox unavailable: {exc}") from exc


async def _create_branch(args: BranchArgs, rt: ToolRuntime) -> WriteResult:
    return await rt.host.create_branch(rt.full_name, args.branch, rt.default_branch)


async def _commit(args: CommitArgs, rt: ToolRuntime) -> WriteResult:
    return await rt.host.commit_files(rt.full_name, args.branch, args.files, args.message)


async def _open_pull(args: PullArgs, rt: ToolRuntime) -> WriteResult:
    return await rt.host.open_draft_pull(rt.full_name, args.head, rt.default_branch, args.title, args.body)


async def _create_issue(args: IssueArgs, rt: ToolRuntime) -> WriteResult:
    return await rt.host.create_issue(rt.full_name, args.title, args.body, args.labels)


async def _comment(args: CommentArgs, rt: ToolRuntime) -> WriteResult:
    return await rt.host.comment(rt.full_name, args.number, args.body)


def register_builtin_tools(registry: ToolRegistry, *, sandbox_timeout_s: float = 300) -> None:
    def spec(
        name: str,
        desc: str,
        perm: Permission,
        handler: Any,
        inp: type[BaseModel],
        out: type[BaseModel] | None,
        timeout: float | None = None,
        actions: frozenset[str] = frozenset(),
    ) -> None:
        registry.register(
            ToolSpec(
                name=name,
                description=desc,
                permission=perm,
                handler=handler,
                input_model=inp,
                output_model=out,
                timeout_s=timeout,
                approval_actions=actions,
            )
        )

    pr = frozenset({"open_pull_request"})
    spec("repo.tree", "List files in the repository revision", Permission.READ, _tree, NoArgs, TreeResult)
    spec(
        "repo.search",
        "Rank repository chunks by relevance to search terms",
        Permission.READ,
        _search,
        SearchArgs,
        SearchResult,
        timeout=60,
    )
    spec("repo.read_file", "Read a bounded line range of a file", Permission.READ, _read_file, ReadFileArgs, FileResult)
    spec("github.list_issues", "List repository issues", Permission.READ, _list_issues, ListIssuesArgs, IssueList)
    spec("github.get_issue", "Read one issue", Permission.READ, _get_issue, NumberArgs, IssueInfo)
    spec("github.get_pull", "Read one pull request", Permission.READ, _get_pull, NumberArgs, PullDetail)
    spec(
        "github.get_pull_files",
        "Read a pull request's changed files and patches",
        Permission.READ,
        _get_pull_files,
        NumberArgs,
        PullDetail,
    )
    spec("github.ci_status", "Read CI check runs for a ref", Permission.READ, _ci_status, RefArgs, CIStatus)
    spec(
        "memory.recall",
        "Retrieve relevant long-term memories for this repository",
        Permission.READ,
        _recall,
        RecallArgs,
        RecallResult,
    )
    spec(
        "sandbox.run_tests",
        "Run the configured test command in an isolated container",
        Permission.EXECUTE,
        _run_tests,
        RunTestsArgs,
        SandboxResult,
        timeout=sandbox_timeout_s + 30,
    )
    spec(
        "github.create_branch",
        "Create an orchestra/* branch from the default branch",
        Permission.WRITE,
        _create_branch,
        BranchArgs,
        WriteResult,
        actions=pr,
    )
    spec(
        "github.commit_files",
        "Commit files to an orchestra/* branch",
        Permission.WRITE,
        _commit,
        CommitArgs,
        WriteResult,
        actions=pr,
    )
    spec(
        "github.open_draft_pull",
        "Open a draft pull request from an orchestra/* branch",
        Permission.WRITE,
        _open_pull,
        PullArgs,
        WriteResult,
        actions=pr,
    )
    spec(
        "github.create_issue",
        "Create an issue",
        Permission.WRITE,
        _create_issue,
        IssueArgs,
        WriteResult,
        actions=frozenset({"create_issue"}),
    )
    spec(
        "github.comment",
        "Comment on an issue or pull request",
        Permission.WRITE,
        _comment,
        CommentArgs,
        WriteResult,
        actions=frozenset({"comment_on_issue"}),
    )
