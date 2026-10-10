# Third-party notices

Orchestra AI depends on the open-source packages below. They are installed from their
public registries (PyPI, npm) and are not vendored into this repository. Each package is
distributed under its own license, which applies to that package; versions are those
pinned in `services/api/uv.lock` and `apps/web/package-lock.json` at the time of writing.
Transitive dependencies are listed in those lock files.

## API service (`services/api`)

| Package | Version | License |
| --- | --- | --- |
| aiosqlite | 0.22.1 | MIT |
| alembic | 1.20.0 | MIT |
| anthropic | 1.13.0 | MIT |
| asyncpg | 0.32.0 | Apache-2.0 |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause |
| fastapi | 0.143.0 | MIT |
| httpx | 0.28.1 | BSD-3-Clause |
| jsonschema | 4.26.0 | MIT |
| langgraph | 1.2.14 | MIT |
| langgraph-checkpoint-postgres | 3.1.2 | MIT |
| langgraph-checkpoint-sqlite | 3.1.1 | MIT |
| mcp | 2.3.0 | MIT |
| psycopg | 3.3.6 | LGPL-3.0-only (used unmodified as a dynamically imported library) |
| pydantic | 2.14.0 | MIT |
| pydantic-settings | 2.15.0 | MIT |
| sqlalchemy | 2.1.4 | MIT |
| uvicorn | 0.54.0 | BSD-3-Clause |

Development only: pytest, pytest-asyncio, respx, ruff, mypy (MIT / Apache-2.0 / BSD).

## Web console (`apps/web`)

| Package | Version | License |
| --- | --- | --- |
| next | 16.4.0 | MIT |
| react, react-dom | 19.3.0 | MIT |
| @xyflow/react | 12.12.0 | MIT |
| swr | 2.5.1 | MIT |
| lucide-react | 1.55.0 | ISC |
| clsx | 2.1.1 | MIT |
| geist (fonts) | 1.7.2 | SIL Open Font License 1.1 |
| tailwindcss | 4.3.3 | MIT |

Development only: typescript (Apache-2.0), vitest (MIT), @testing-library/* (MIT),
@playwright/test (Apache-2.0), openapi-typescript (MIT), eslint (MIT).

## Container base images

`python:3.12-slim`, `node:22-alpine`, `postgres:16-alpine`, and the `uv` binary from
`ghcr.io/astral-sh/uv` (MIT OR Apache-2.0) are pulled at build time under their own terms.

## Conceptual inspiration

See [NOTICE.md](NOTICE.md) for the provenance of this project. No code or assets from
other projects are included.
