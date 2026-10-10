"""Live GitHub REST client (least privilege, no destructive operations)."""

from __future__ import annotations

import asyncio
import base64
import random
from typing import Any

import httpx

from orchestra.integrations.github.protocol import (
    CheckRun,
    CIStatus,
    CodeHostError,
    FileDiff,
    IssueInfo,
    PullInfo,
    RepoInfo,
    TreeEntry,
    WriteResult,
    assert_safe_branch,
)
from orchestra.observability import get_logger

log = get_logger(__name__)

_TRANSIENT = {408, 429, 500, 502, 503, 504}


class GitHubClient:
    simulated = False

    def __init__(
        self,
        token: str,
        base_url: str = "https://api.github.com",
        timeout_s: float = 20.0,
        max_attempts: int = 4,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_s,
            transport=transport,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "orchestra-ai",
            },
        )
        self._max_attempts = max_attempts

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- transport --------------------------------------------------------------------

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        delay = 0.5
        for attempt in range(1, self._max_attempts + 1):
            try:
                resp = await self._client.request(method, url, **kwargs)
            except httpx.TransportError as exc:
                if attempt == self._max_attempts:
                    raise CodeHostError(f"GitHub unreachable: {exc.__class__.__name__}", transient=True) from exc
            else:
                secondary_limit = resp.status_code == 403 and resp.headers.get("x-ratelimit-remaining") == "0"
                if resp.status_code not in _TRANSIENT and not secondary_limit:
                    if resp.status_code >= 400:
                        message = _error_message(resp)
                        raise CodeHostError(f"GitHub {method} {url} -> {resp.status_code}: {message}", resp.status_code)
                    return resp
                if attempt == self._max_attempts:
                    raise CodeHostError(
                        f"GitHub {method} {url} -> {resp.status_code} after {attempt} attempts",
                        resp.status_code,
                        transient=True,
                    )
                retry_after = resp.headers.get("retry-after")
                if retry_after and retry_after.isdigit():
                    delay = min(float(retry_after), 30.0)
            log.warning("github request retry", extra={"attempt": attempt, "url": url})
            await asyncio.sleep(delay + random.uniform(0, delay / 2))
            delay = min(delay * 2, 16.0)
        raise AssertionError("unreachable")

    async def _json(self, method: str, url: str, **kwargs: Any) -> Any:
        return (await self._request(method, url, **kwargs)).json()

    # -- reads ------------------------------------------------------------------------

    async def get_user(self) -> dict[str, Any]:
        data = await self._json("GET", "/user")
        return {"login": data["login"], "name": data.get("name"), "html_url": data.get("html_url")}

    async def get_repo(self, full_name: str) -> RepoInfo:
        return _repo(await self._json("GET", f"/repos/{full_name}"))

    async def list_repos(self, page: int = 1, per_page: int = 30) -> list[RepoInfo]:
        data = await self._json("GET", "/user/repos", params={"page": page, "per_page": per_page, "sort": "pushed"})
        return [_repo(r) for r in data]

    async def get_tree(self, full_name: str, ref: str) -> list[TreeEntry]:
        data = await self._json("GET", f"/repos/{full_name}/git/trees/{ref}", params={"recursive": "1"})
        return [TreeEntry(path=e["path"], type=e["type"], size=e.get("size")) for e in data.get("tree", [])]

    async def read_file(self, full_name: str, path: str, ref: str) -> str:
        data = await self._json("GET", f"/repos/{full_name}/contents/{path}", params={"ref": ref})
        if isinstance(data, list) or data.get("encoding") != "base64":
            raise CodeHostError(f"{path} is not a regular file")
        return base64.b64decode(data["content"]).decode("utf-8", errors="replace")

    async def list_issues(self, full_name: str, state: str = "open", limit: int = 30) -> list[IssueInfo]:
        data = await self._json(
            "GET", f"/repos/{full_name}/issues", params={"state": state, "per_page": min(limit, 100)}
        )
        return [_issue(i) for i in data if "pull_request" not in i]

    async def get_issue(self, full_name: str, number: int) -> IssueInfo:
        return _issue(await self._json("GET", f"/repos/{full_name}/issues/{number}"))

    async def list_pulls(self, full_name: str, state: str = "open", limit: int = 30) -> list[PullInfo]:
        data = await self._json(
            "GET", f"/repos/{full_name}/pulls", params={"state": state, "per_page": min(limit, 100)}
        )
        return [_pull(p) for p in data]

    async def get_pull(self, full_name: str, number: int) -> PullInfo:
        return _pull(await self._json("GET", f"/repos/{full_name}/pulls/{number}"))

    async def get_pull_files(self, full_name: str, number: int) -> list[FileDiff]:
        data = await self._json("GET", f"/repos/{full_name}/pulls/{number}/files", params={"per_page": 100})
        return [
            FileDiff(
                filename=f["filename"],
                status=f["status"],
                additions=f.get("additions", 0),
                deletions=f.get("deletions", 0),
                patch=f.get("patch"),
            )
            for f in data
        ]

    async def get_ci_status(self, full_name: str, ref: str) -> CIStatus:
        data = await self._json("GET", f"/repos/{full_name}/commits/{ref}/check-runs", params={"per_page": 50})
        runs = [
            CheckRun(name=r["name"], status=r["status"], conclusion=r.get("conclusion"), html_url=r.get("html_url"))
            for r in data.get("check_runs", [])
        ]
        if not runs:
            state = "none"
        elif any(r.status != "completed" for r in runs):
            state = "pending"
        elif all(r.conclusion in {"success", "skipped", "neutral"} for r in runs):
            state = "success"
        else:
            state = "failure"
        return CIStatus(ref=ref, state=state, checks=runs)

    async def _branch_sha(self, full_name: str, branch: str) -> str | None:
        try:
            data = await self._json("GET", f"/repos/{full_name}/git/ref/heads/{branch}")
        except CodeHostError as exc:
            if exc.status == 404:
                return None
            raise
        return data["object"]["sha"]

    # -- writes (only reachable through approved actions) ----------------------------

    async def create_branch(self, full_name: str, branch: str, from_ref: str) -> WriteResult:
        repo = await self.get_repo(full_name)
        assert_safe_branch(branch, repo.default_branch)
        existing = await self._branch_sha(full_name, branch)
        if existing:
            return WriteResult(kind="branch", url=None, ref=branch, reused=True)
        base_sha = await self._branch_sha(full_name, from_ref)
        if base_sha is None:
            raise CodeHostError(f"Base ref {from_ref!r} not found")
        await self._json("POST", f"/repos/{full_name}/git/refs", json={"ref": f"refs/heads/{branch}", "sha": base_sha})
        return WriteResult(kind="branch", url=None, ref=branch)

    async def commit_files(self, full_name: str, branch: str, files: dict[str, str], message: str) -> WriteResult:
        repo = await self.get_repo(full_name)
        assert_safe_branch(branch, repo.default_branch)
        head_sha = await self._branch_sha(full_name, branch)
        if head_sha is None:
            raise CodeHostError(f"Branch {branch!r} does not exist")
        head_commit = await self._json("GET", f"/repos/{full_name}/git/commits/{head_sha}")
        tree_items = []
        for path, content in files.items():
            blob = await self._json(
                "POST", f"/repos/{full_name}/git/blobs", json={"content": content, "encoding": "utf-8"}
            )
            tree_items.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        tree = await self._json(
            "POST", f"/repos/{full_name}/git/trees", json={"base_tree": head_commit["tree"]["sha"], "tree": tree_items}
        )
        if tree["sha"] == head_commit["tree"]["sha"]:
            return WriteResult(kind="commit", url=None, ref=head_sha, reused=True)
        commit = await self._json(
            "POST",
            f"/repos/{full_name}/git/commits",
            json={"message": message, "tree": tree["sha"], "parents": [head_sha]},
        )
        # force=False: a non-fast-forward update is rejected rather than rewriting history.
        await self._json(
            "PATCH", f"/repos/{full_name}/git/refs/heads/{branch}", json={"sha": commit["sha"], "force": False}
        )
        return WriteResult(kind="commit", url=commit.get("html_url"), ref=commit["sha"])

    async def open_draft_pull(self, full_name: str, head: str, base: str, title: str, body: str) -> WriteResult:
        repo = await self.get_repo(full_name)
        assert_safe_branch(head, repo.default_branch)
        owner = full_name.split("/")[0]
        existing = await self._json(
            "GET", f"/repos/{full_name}/pulls", params={"head": f"{owner}:{head}", "state": "open"}
        )
        if existing:
            return WriteResult(
                kind="pull_request", url=existing[0]["html_url"], number=existing[0]["number"], reused=True
            )
        pr = await self._json(
            "POST",
            f"/repos/{full_name}/pulls",
            json={"title": title, "head": head, "base": base, "body": body, "draft": True},
        )
        return WriteResult(kind="pull_request", url=pr["html_url"], number=pr["number"], ref=head)

    async def create_issue(self, full_name: str, title: str, body: str, labels: list[str]) -> WriteResult:
        issue = await self._json(
            "POST", f"/repos/{full_name}/issues", json={"title": title, "body": body, "labels": labels}
        )
        return WriteResult(kind="issue", url=issue["html_url"], number=issue["number"])

    async def comment(self, full_name: str, number: int, body: str) -> WriteResult:
        c = await self._json("POST", f"/repos/{full_name}/issues/{number}/comments", json={"body": body})
        return WriteResult(kind="comment", url=c["html_url"], number=number)


def _error_message(resp: httpx.Response) -> str:
    try:
        return str(resp.json().get("message", ""))[:300]
    except ValueError:
        return resp.text[:300]


def _repo(r: dict[str, Any]) -> RepoInfo:
    return RepoInfo(
        full_name=r["full_name"],
        description=r.get("description"),
        default_branch=r.get("default_branch", "main"),
        visibility=r.get("visibility") or ("private" if r.get("private") else "public"),
        html_url=r.get("html_url"),
        language=r.get("language"),
        stars=r.get("stargazers_count", 0),
        open_issues=r.get("open_issues_count", 0),
        pushed_at=r.get("pushed_at"),
    )


def _issue(i: dict[str, Any]) -> IssueInfo:
    return IssueInfo(
        number=i["number"],
        title=i["title"],
        body=i.get("body"),
        state=i["state"],
        labels=[lbl["name"] if isinstance(lbl, dict) else str(lbl) for lbl in i.get("labels", [])],
        author=(i.get("user") or {}).get("login"),
        html_url=i.get("html_url"),
        created_at=i.get("created_at"),
        is_pull_request="pull_request" in i,
    )


def _pull(p: dict[str, Any]) -> PullInfo:
    return PullInfo(
        number=p["number"],
        title=p["title"],
        body=p.get("body"),
        state=p["state"],
        head=p["head"]["ref"],
        base=p["base"]["ref"],
        draft=p.get("draft", False),
        author=(p.get("user") or {}).get("login"),
        html_url=p.get("html_url"),
        created_at=p.get("created_at"),
    )
