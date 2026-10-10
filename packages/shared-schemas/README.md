# shared-schemas

`openapi.json` is the API contract between `services/api` and `apps/web`. It is
generated from the FastAPI app, committed, and checked in CI:

```bash
cd services/api && uv run python -m orchestra.api.openapi_export ../../packages/shared-schemas/openapi.json
cd apps/web && npm run gen:api   # regenerates src/lib/api/schema.d.ts
```
