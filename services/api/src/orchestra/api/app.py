"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from orchestra import __version__
from orchestra.api.middleware import RateLimitMiddleware, RequestContextMiddleware
from orchestra.api.routes import approvals, repositories, system, workflows
from orchestra.config import Settings, get_settings
from orchestra.observability import configure_logging, get_logger
from orchestra.services import Services

log = get_logger(__name__)

DESCRIPTION = """
Orchestra AI turns engineering requests into structured, multi-agent execution plans,
runs them through a LangGraph workflow, and pauses for human approval before any
external write.

**Authentication:** every endpoint except `/api/health` requires `Authorization: Bearer <token>`.
Roles are hierarchical: `viewer` < `operator` (create/cancel workflows, connect repositories)
< `approver` (approve/reject actions) < `admin` (settings).
"""


def create_app(settings: Settings | None = None, services: Services | None = None) -> FastAPI:
    settings = settings or get_settings()
    if not any((settings.admin_token, settings.approver_token, settings.operator_token, settings.viewer_token)):
        raise RuntimeError("Configure at least one API token (e.g. ORCHESTRA_ADMIN_TOKEN); see .env.example")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        svc = services or Services(settings)
        app.state.services = svc
        await svc.startup()
        log.info("orchestra started", extra={"mode": settings.mode.value, "version": __version__})
        try:
            yield
        finally:
            log.info("orchestra shutting down")
            await svc.shutdown()

    app = FastAPI(
        title="Orchestra AI API",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        openapi_tags=[
            {"name": "workflows"},
            {"name": "approvals"},
            {"name": "repositories"},
            {"name": "system"},
            {"name": "github"},
        ],
    )
    app.add_middleware(RateLimitMiddleware, per_minute=settings.rate_limit_per_minute)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID", "Last-Event-ID"],
    )
    app.add_middleware(RequestContextMiddleware)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            {
                "error": _code(exc.status_code),
                "detail": exc.detail,
                "request_id": getattr(request.state, "request_id", None),
            },
            status_code=exc.status_code,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            {
                "error": "validation_error",
                "detail": exc.errors(),
                "request_id": getattr(request.state, "request_id", None),
            },
            status_code=422,
        )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error")
        return JSONResponse(
            {
                "error": "internal_error",
                "detail": "An internal error occurred",
                "request_id": getattr(request.state, "request_id", None),
            },
            status_code=500,
        )

    for router in (workflows.router, approvals.router, repositories.router, system.router):
        app.include_router(router)
    return app


def _code(status: int) -> str:
    return {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        422: "validation_error",
        429: "rate_limited",
        502: "upstream_error",
    }.get(status, "error")


def main() -> None:
    import uvicorn

    settings = get_settings()
    configure_logging()
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_config=None,
        timeout_graceful_shutdown=15,
    )
