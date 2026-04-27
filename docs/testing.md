# Testing

The project favors integration tests over narrow mocks. The fake OpenAI gateway in `tests/conftest.py` is used where the test is validating app behavior rather than OpenAI itself.

## Main Checks

Run these before committing backend or full-stack changes:

```bash
./.venv/bin/pyright
./.venv/bin/pytest
npm run typecheck
npm run build
```

For browser coverage:

```bash
npm run test:e2e -- --project=chromium-desktop
npm run test:e2e -- --project=chromium-mobile
```

## Playwright

Playwright starts both the FastAPI backend and Vite frontend.

- It reads `.env` when present.
- It requires live `OPENAI_API_KEY`, `APP_SIGNING_SECRET`, and S3-compatible storage values.
- It disables Clerk for browser tests with test-only env overrides and uses local-dev bearer auth.
- The desktop project runs the live ChatKit flow: upload through the explorer, select the file for ChatKit scope, ask grounded QA, verify the task input includes the source ID, and delete the source.
- The mobile project runs the shell/layout checks and skips the live ChatKit upload flow.

Artifacts are written under `output/playwright/`, which is ignored.

## Contract Tests

`tests/integration/test_app_contracts.py` checks:

- REST route names against the capability matrix.
- Frontend TypeScript fields against FastAPI OpenAPI schemas.
- MCP tool discovery, destructive hints, and MCP Apps UI resource metadata.
- The local FastMCP dev entrypoint exports the same tool surface as the authenticated server.
- ChatKit tool parity.
- ChatKit attachment and thread/task linkage.
- Upload, source-level vector search, QA, tag/path filtering, reindexing, cleanup, and MCP file ingest flows.

## FastMCP Dev Tools

The dev entrypoint is `backend/app/mcp/dev_server.py:mcp`. It intentionally skips the production bearer-token verifier while keeping the same services, settings, database, and storage paths as the backend.

```bash
./.venv/bin/fastmcp dev apps backend/app/mcp/dev_server.py:mcp --mcp-port 8001 --dev-port 8080 --no-reload
./.venv/bin/fastmcp dev inspector backend/app/mcp/dev_server.py:mcp --ui-port 6274 --server-port 6277 --no-reload
```

Use this before deployment to verify tool discovery, Apps UI rendering, research actions, semantic/tag search, source detail views, and raw-file/content retrieval against a realistic local library.

`tests/test_migrations.py` checks:

- Alembic head can upgrade a temporary database.
- Migrated tables and columns match `Base.metadata`.
- `DatabaseManager` can bootstrap with `DATABASE_SCHEMA_MODE=migrations`.

## Live Test Cleanup

The live Playwright test deletes the source it uploads. If a run is interrupted, search for `chatkit-live-pw-` sources in the local app and delete them through the app API or UI.
