# Contributing

## Layout

| Path | What lives there |
| --- | --- |
| `services/api` | FastAPI + LangGraph backend (`src/orchestra`), tests, Alembic migrations |
| `apps/web` | Next.js console, Vitest tests (`src/**/*.test.tsx`), Playwright E2E (`e2e/`) |
| `packages/shared-schemas` | Committed OpenAPI contract |
| `infrastructure/docker` | API, web and sandbox Dockerfiles |
| `examples` | Example MCP server and configuration |
| `docs` | Topic documentation |

## Setup

Requirements: Python 3.12 with [uv](https://docs.astral.sh/uv/), Node.js 20.9+ (22 recommended),
Docker (for the sandbox and compose stack).

```bash
make install        # uv sync + npm ci
make sandbox-image  # optional: enables the Docker sandbox tests
```

## Checks

Run everything CI runs before opening a pull request:

```bash
make check          # API: ruff, mypy, pytest | web: typecheck, lint, vitest, build
make e2e            # Playwright against a real API in demo mode
```

## Changing the API

1. Change models/routes in `services/api`.
2. If the database schema changed:
   `cd services/api && uv run alembic revision --autogenerate -m "describe change"`, review the
   generated file, then `uv run alembic upgrade head && uv run alembic check`.
3. Regenerate the contract and client types: `make openapi`.

## Adding an agent capability or tool

- New tools go in `tools/builtin.py` with Pydantic input/output models and the narrowest
  `Permission`. Grant them to agents in `tools/policy.py`.
- Anything with external side effects must be a `write` tool reached only through an approved
  `ProposedAction` (see `agents/automation.py`). Add the action type to the tool's
  `approval_actions`.
- Agent outputs need a schema in `schemas/agents.py` and a deterministic demo implementation.
  Demo output must stay honest: it is labeled simulated in the UI.

## Commits

Conventional commit messages (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`), one
logical change per commit.
