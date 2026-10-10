from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select

from orchestra.api.deps import get_or_404, get_services
from orchestra.db.models import Repository, Workflow
from orchestra.demo.host import DEMO_REPO
from orchestra.integrations.github.protocol import CodeHost, CodeHostError, IssueInfo, PullInfo
from orchestra.retrieval import RepositoryReader
from orchestra.schemas.api import FileContent, Page, RepositoryConnect, RepositoryOut, WorkflowSummary
from orchestra.security.auth import Principal, Role, require
from orchestra.services import Services

router = APIRouter(prefix="/api/repositories", tags=["repositories"])


def _out(repo: Repository) -> RepositoryOut:
    out = RepositoryOut.model_validate(repo)
    out.simulated = repo.provider == "demo"
    return out


def _host(services: Services, repo: Repository) -> CodeHost:
    try:
        return services.host_for(repo)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


async def _call(coro: Any) -> Any:
    try:
        return await coro
    except CodeHostError as exc:
        raise HTTPException(502 if exc.status is None or exc.status >= 500 else exc.status, str(exc)) from exc


@router.get("", response_model=Page[RepositoryOut])
async def list_repositories(
    services: Services = Depends(get_services),
    _: Principal = Depends(require(Role.VIEWER)),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> Page[RepositoryOut]:
    async with services.db.session() as s:
        total = await s.scalar(select(func.count()).select_from(Repository))
        rows = (
            (await s.execute(select(Repository).order_by(Repository.connected_at.desc()).limit(limit).offset(offset)))
            .scalars()
            .all()
        )
    return Page(items=[_out(r) for r in rows], total=total or 0, limit=limit, offset=offset)


@router.post("/connect", response_model=RepositoryOut, status_code=201)
async def connect_repository(
    body: RepositoryConnect, services: Services = Depends(get_services), _: Principal = Depends(require(Role.OPERATOR))
) -> RepositoryOut:
    if body.provider == "demo":
        if body.full_name != DEMO_REPO:
            raise HTTPException(422, f"The demo provider only serves {DEMO_REPO}")
        host: CodeHost = services.demo_host
    else:
        client = services.github()
        if client is None:
            raise HTTPException(409, "GitHub is not connected. Add a token in Settings, or use the demo repository.")
        host = client
    info = await _call(host.get_repo(body.full_name))
    async with services.db.session() as s:
        existing = (
            (
                await s.execute(
                    select(Repository).where(
                        Repository.provider == body.provider, Repository.full_name == info.full_name
                    )
                )
            )
            .scalars()
            .first()
        )
        repo = existing or Repository(provider=body.provider, full_name=info.full_name)
        repo.default_branch, repo.description = info.default_branch, info.description
        repo.visibility, repo.html_url = info.visibility, info.html_url
        s.add(repo)
    return _out(repo)


@router.get("/{repository_id}", response_model=RepositoryOut)
async def get_repository(
    repository_id: str, services: Services = Depends(get_services), _: Principal = Depends(require(Role.VIEWER))
) -> RepositoryOut:
    async with services.db.session() as s:
        return _out(await get_or_404(s, Repository, repository_id, "Repository"))


@router.get("/{repository_id}/tree")
async def repository_tree(
    repository_id: str, services: Services = Depends(get_services), _: Principal = Depends(require(Role.VIEWER))
) -> dict[str, Any]:
    async with services.db.session() as s:
        repo = await get_or_404(s, Repository, repository_id, "Repository")
    reader = RepositoryReader(_host(services, repo), repo.full_name, repo.default_branch)
    snapshot = await _call(reader.snapshot())
    return {
        "files": [e.path for e in snapshot.files][:5000],
        "languages": snapshot.languages,
        "dependencies": snapshot.dependencies,
        "entry_points": snapshot.entry_points,
        "docs": snapshot.docs,
        "tests": snapshot.tests,
    }


@router.get("/{repository_id}/files", response_model=FileContent)
async def repository_file(
    repository_id: str,
    path: str = Query(min_length=1, max_length=500),
    services: Services = Depends(get_services),
    _: Principal = Depends(require(Role.VIEWER)),
) -> FileContent:
    async with services.db.session() as s:
        repo = await get_or_404(s, Repository, repository_id, "Repository")
    reader = RepositoryReader(_host(services, repo), repo.full_name, repo.default_branch)
    content = await _call(reader.read(path))
    reason = None if content is not None else (reader.skipped[-1] if reader.skipped else "not found")
    return FileContent(path=path, content=content, skipped_reason=reason)


@router.get("/{repository_id}/issues", response_model=list[IssueInfo])
async def repository_issues(
    repository_id: str,
    state: str = Query("open", pattern="^(open|closed|all)$"),
    services: Services = Depends(get_services),
    _: Principal = Depends(require(Role.VIEWER)),
) -> list[IssueInfo]:
    async with services.db.session() as s:
        repo = await get_or_404(s, Repository, repository_id, "Repository")
    return await _call(_host(services, repo).list_issues(repo.full_name, state, 50))


@router.get("/{repository_id}/pulls", response_model=list[PullInfo])
async def repository_pulls(
    repository_id: str,
    state: str = Query("open", pattern="^(open|closed|all)$"),
    services: Services = Depends(get_services),
    _: Principal = Depends(require(Role.VIEWER)),
) -> list[PullInfo]:
    async with services.db.session() as s:
        repo = await get_or_404(s, Repository, repository_id, "Repository")
    return await _call(_host(services, repo).list_pulls(repo.full_name, state, 50))


@router.get("/{repository_id}/ci")
async def repository_ci(
    repository_id: str,
    ref: str | None = None,
    services: Services = Depends(get_services),
    _: Principal = Depends(require(Role.VIEWER)),
) -> dict[str, Any]:
    async with services.db.session() as s:
        repo = await get_or_404(s, Repository, repository_id, "Repository")
    status = await _call(_host(services, repo).get_ci_status(repo.full_name, ref or repo.default_branch))
    return status.model_dump()


@router.post("/{repository_id}/analyze", response_model=WorkflowSummary, status_code=202)
async def analyze_repository(
    repository_id: str,
    services: Services = Depends(get_services),
    principal: Principal = Depends(require(Role.OPERATOR)),
) -> WorkflowSummary:
    from orchestra.api.routes.workflows import _summary

    async with services.db.session() as s:
        repo = await get_or_404(s, Repository, repository_id, "Repository")
        wf = Workflow(
            title=f"Analyze {repo.full_name}",
            request=f"Analyze the architecture of {repo.full_name}: summarise modules, entry points, "
            "dependencies and documentation.",
            mode=services.settings.mode.value,
            repository_id=repo.id,
            created_by=principal.name,
        )
        s.add(wf)
    services.runner.submit(wf.id)
    return _summary(wf)
