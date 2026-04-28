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
- It requires live `OPENAI_API_KEY` and S3-compatible storage values.
- It disables Clerk for browser tests with test-only env overrides and uses local-dev bearer auth.
- The desktop project runs the live ChatKit flow: upload through the explorer, refer to the file explicitly from ChatKit, ask grounded QA, verify the task input includes the source ID, and delete the source.
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

## Open RAGBench Retrieval Eval

The Open RAGBench PDF eval builds a deterministic 100-document local corpus from `vectara/open_ragbench`, uploads original arXiv PDFs to a running app, and scores document-level retrieval through `/api/search`.

```bash
./.venv/bin/openai-vectorstore2-open-ragbench-eval setup-upload
./.venv/bin/openai-vectorstore2-open-ragbench-eval run .local/evals/open_ragbench/<run-id>
```

Artifacts are written under `.local/evals/open_ragbench/`, while the latest lightweight report artifacts are mirrored to `evals/open_ragbench/latest/` for review in Git. Reruns are progressive: `setup-upload` reuses `subset.json` when present, downloads each missing PDF into the local run directory, uploads it immediately, and reuses completed records from `uploads.json` instead of reingesting the same document. Delete or edit a failed record to retry just that document. The `run` command writes `summary.md`, `demo_queries.md`, `detailed_metrics.md`, and `results.json`, including five sampled QA answer evaluations by default. Retrieval queries run with bounded parallelism and default to 50 concurrent API calls.

`tests/test_migrations.py` checks:

- Alembic head can upgrade a temporary database.
- Migrated tables and columns match `Base.metadata`.
- `DatabaseManager` can bootstrap with `DATABASE_SCHEMA_MODE=migrations`.

## Live Test Cleanup

The live Playwright test deletes the source it uploads. If a run is interrupted, search for `chatkit-live-pw-` sources in the local app and delete them through the app API or UI.
