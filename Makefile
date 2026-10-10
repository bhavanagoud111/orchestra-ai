.PHONY: install api web dev check check-api check-web e2e openapi sandbox-image up down

install:
	cd services/api && uv sync
	cd apps/web && npm ci

api:  ## API on :8000 (demo mode); needs ORCHESTRA_ADMIN_TOKEN in services/api/.env or the environment
	cd services/api && uv run orchestra-api

web:  ## Web console on :3000; needs ORCHESTRA_API_TOKEN
	cd apps/web && npm run dev

check: check-api check-web

check-api:
	cd services/api && uv run ruff check src tests && uv run ruff format --check src && uv run mypy && uv run pytest -q

check-web:
	cd apps/web && npm run typecheck && npm run lint && npm test && npm run build

e2e:
	cd apps/web && npm run build && npx playwright test

openapi:
	cd services/api && uv run python -m orchestra.api.openapi_export ../../packages/shared-schemas/openapi.json
	cd apps/web && npm run gen:api

sandbox-image:
	docker build -t orchestra-sandbox-python:3.12 -f infrastructure/docker/sandbox-python.Dockerfile infrastructure/docker

up:
	docker compose up --build -d

down:
	docker compose down
