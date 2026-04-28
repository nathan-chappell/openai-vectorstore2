# OpenAI Vectorstore2

OpenAI Vectorstore2 is an app-first file explorer backed by OpenAI vector-store indexing and search. The backend owns ingestion, tagging, virtual paths, source-level indexing, retrieval, and generation workflows. ChatKit is the primary frontend surface, while MCP exposes the same app functionality to MCP hosts and MCP Apps UIs.

## Shape

- Backend: FastAPI, SQLAlchemy, pydantic-settings, OpenAI Responses/vector stores, ChatKit server, FastMCP.
- Frontend: Vite, React, TypeScript, Clerk, ChatKit, Playwright.
- Storage: local file storage by default, with an S3-compatible adapter for deployment.
- Retrieval: normal ingestion publishes source-level files into OpenAI vector stores with app-owned attributes for source ID, path, type, one representative tag, and created date. Optional semantic split records can be generated explicitly for inspection.
- Schema: Alembic migrations are the default. `create_all` is only for empty throwaway databases because it does not alter existing tables.

## Core Workflows

- Upload PDFs, text files, and audio/video conversation recordings.
- Store files in virtual folders and publish source-level OpenAI vector-store indexes.
- Search with source, kind, tag, path, and creation-time filters.
- Run QA, free-form generation, image generation, voice generation, and branch search over indexed source-file matches.
- Use the web workspace for Explorer navigation, Library semantic/tag search, Results references, preview, and explicit ChatKit `@` file references.
- Use ChatKit as the main agentic web UI and MCP as an adapter over the same service layer.

## Local Development

1. Create `.env` from `.env.example`. Keep optional defaults out unless you actually need them.
2. Install Python dependencies into `.venv`, then install the package in editable mode if needed.
3. Run `npm install`.
4. Run `npm run build:watch`.
5. Start the backend with `./.venv/bin/openai-vectorstore2-http`.
6. Open `http://localhost:8000`.
7. Point MCP hosts at `http://localhost:8000/mcp/`.

Backend logs are written to `.local/logs/openai-vectorstore2.log` by default.

VS Code users can press F5 to start the backend debugger. The workspace task `npm: build:watch` keeps the frontend bundle fresh for the backend-served app.

## MCP Development

The production HTTP app mounts authenticated MCP at `/mcp/`. For local FastMCP tooling, use the unauthenticated dev entrypoint:

```bash
./.venv/bin/fastmcp dev apps backend/app/mcp/dev_server.py:mcp --mcp-port 8001 --dev-port 8080 --no-reload
./.venv/bin/fastmcp dev inspector backend/app/mcp/dev_server.py:mcp --ui-port 6274 --server-port 6277 --no-reload
```

This entrypoint uses the same app services and `.env` settings as the backend, so local tool calls read and write the same development database and storage.

## Verification

- `./.venv/bin/pytest`
- `./.venv/bin/pyright`
- `npm run typecheck`
- `npm run build`
- `npm run test:e2e -- --project=chromium-desktop`
- `npm run test:e2e -- --project=chromium-mobile`

Playwright uses live OpenAI and S3-compatible storage from `.env`, while Clerk is disabled through test-only overrides and local-dev auth is enabled.

## More Docs

- [Architecture](docs/architecture.md)
- [Auth](docs/auth.md)
- [Admin Integration](docs/admin-integration.md)
- [Deployment](docs/deployment.md)
- [Security](docs/security.md)
- [Testing](docs/testing.md)
- [Migrations](docs/migrations.md)
- [Operations](docs/operations.md)
