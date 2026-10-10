# Orchestra AI API (FastAPI + LangGraph). Build context: repository root.
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
COPY --from=ghcr.io/astral-sh/uv:0.10.4 /uv /usr/local/bin/uv
WORKDIR /app

FROM base AS build
COPY services/api/pyproject.toml services/api/uv.lock services/api/README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY services/api/src ./src
COPY services/api/migrations ./migrations
COPY services/api/alembic.ini ./
RUN uv sync --frozen --no-dev

FROM base
RUN useradd --create-home --uid 10001 orchestra
COPY --from=build --chown=orchestra:orchestra /app /app
COPY --chown=orchestra:orchestra examples /app/examples
ENV PATH="/app/.venv/bin:$PATH" \
    ORCHESTRA_HOST=0.0.0.0 \
    ORCHESTRA_PORT=8000 \
    ORCHESTRA_DATA_DIR=/app/data \
    ORCHESTRA_AUTO_CREATE_SCHEMA=false
RUN mkdir -p /app/data && chown orchestra:orchestra /app/data
USER orchestra
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)" || exit 1
CMD ["sh", "-c", "alembic upgrade head && exec orchestra-api"]
