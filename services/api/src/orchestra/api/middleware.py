"""HTTP middleware: correlation IDs, access logs, rate limiting."""

from __future__ import annotations

import hashlib
import re
import time
import uuid
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from orchestra.observability import get_logger, request_id_var

log = get_logger("orchestra.http")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if _REQUEST_ID.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = request_id
        if request.url.path != "/api/health":
            log.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    "request_id": request_id,
                },
            )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window limit per credential (or client IP when unauthenticated).

    Process-local by design: Orchestra runs as a single API process. Behind multiple
    replicas, enforce limits at the gateway or move this to Redis.
    """

    EXEMPT = ("/api/health", "/docs", "/openapi.json", "/redoc")

    def __init__(self, app, per_minute: int) -> None:
        super().__init__(app)
        self.per_minute = per_minute
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if self.per_minute <= 0 or path.startswith(self.EXEMPT) or path.endswith("/events"):
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        key = (
            hashlib.sha256(auth.encode()).hexdigest()[:16]
            if auth
            else f"ip:{request.client.host if request.client else '?'}"
        )
        now = time.monotonic()
        window = self.hits[key]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= self.per_minute:
            retry = max(1, int(60 - (now - window[0])))
            return JSONResponse(
                {"error": "rate_limited", "detail": f"Limit is {self.per_minute} requests/minute"},
                status_code=429,
                headers={"Retry-After": str(retry)},
            )
        window.append(now)
        return await call_next(request)
