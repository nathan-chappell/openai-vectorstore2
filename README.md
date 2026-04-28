# OpenAI Vectorstore2

OpenAI Vectorstore2 is an app-first research-library workspace backed by OpenAI vector-store indexing and search. Users can upload source files, organize them in a virtual file explorer, search the indexed library with app-owned metadata filters, and use ChatKit to ask grounded questions, build research collections, and save generated artifacts back into the library.

This repository is both a portfolio project and a product demo. Its main technical goal is to show how a typed FastAPI service layer can power multiple surfaces at once: a React file workspace, ChatKit agent tools, and an authenticated MCP server. The project focuses on source-level RAG over user-managed files rather than one-off chat uploads, with persistent storage, task progress, billing/usage accounting foundations, and deployment-ready database and object-storage boundaries.

> Live app: [openai-vectorstore2-production.up.railway.app](https://openai-vectorstore2-production.up.railway.app/)
>
> Access note: signing up creates a Clerk account, but access to the live demo is granted manually. If you would like access or a walkthrough, please reach out via [GitHub](https://github.com/nathan-chappell).

The live deployment runs on Railway with PostgreSQL, S3-compatible object storage, Clerk auth, and a Docker image published as `nathanschappell/openai-vectorstore2`. The app is structured so environment-specific public client settings, including Clerk and ChatKit domain keys, can be provided at runtime by the backend rather than only at frontend build time.

## Technical overview

- Frontend stack: React, Vite, TypeScript, Clerk, `@openai/chatkit`, and `@openai/chatkit-react`
- Backend stack: FastAPI, SQLAlchemy, Alembic, pydantic-settings, OpenAI Responses/vector stores, ChatKit server integration, and FastMCP
- Storage: source files and generated artifacts use local storage by default, with an S3-compatible adapter for deployment
- Retrieval: normal ingestion publishes source-level files into OpenAI vector stores with app-owned attributes for source ID, path, type, representative tag, and created date
- Agent surface: ChatKit tools expose library search, grounded QA, file ingestion, research-library building, report saving, generated assets, and task progress
- MCP surface: authenticated MCP exposes the same service layer to MCP hosts and MCP Apps UIs
- Runtime behavior: startup runs Alembic migrations, logs the app version, retries briefly while PostgreSQL is waking up, and serves the built frontend from FastAPI
- Billing foundation: shared credit, payment, free-credit, and cost-event tables are provided through the `ai-portfolio-admin` submodule

## Core workflows

- Upload PDFs, text files, Markdown, and audio/video conversation recordings
- Store files in virtual folders and search them by query, source kind, tag, path, source id, and created date
- Ask grounded questions over selected or searched sources from ChatKit
- Build small research libraries from topics or papers, dedupe discovered sources, and index the results
- Save structured Markdown reports into the library as searchable source artifacts
- Generate image and voice artifacts from retrieved context
- Use the MCP endpoint at `/mcp/` from compatible MCP clients

## Local setup

### Prerequisites

- Python `3.14`
- Node.js and npm
- An OpenAI API key
- A Clerk application
- Optional S3-compatible object storage for deployment-like testing

### Environment

Create `.env` from `.env.example`. Keep optional defaults out unless you actually need them.

Important values for a realistic local or deployed run:

```bash
OPENAI_API_KEY=your-openai-key
CLERK_SECRET_KEY=your-clerk-secret-key
CLERK_PUBLISHABLE_KEY=your-clerk-publishable-key
CHATKIT_DOMAIN_KEY=your-chatkit-domain-key

APP_BASE_URL=http://localhost:8000
DATABASE_URL=sqlite+aiosqlite:///./.local/openai-vectorstore2.db
DATABASE_SCHEMA_MODE=migrations

STATIC_DIR=frontend/dist
VITE_API_BASE_URL=/api
VITE_CHATKIT_DOMAIN_KEY=domain_pk_local_vectorstore2
```

For deployed PostgreSQL, keep `DATABASE_SCHEMA_MODE=migrations` and set `DATABASE_POSTGRES_SCHEMA=openai_vectorstore2`. Railway should provide `PORT`; do not bind to a fixed host/port that conflicts with Railway's edge proxy.

### Install and run

Run both the Python and npm toolchains from the repository root.

```bash
python3.14 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
npm install
npm run build
./.venv/bin/openai-vectorstore2-http
```

Then open `http://localhost:8000`.

### Frontend development mode

For the backend-served app, `npm run build:watch` keeps `frontend/dist` fresh while the backend runs.

```bash
npm run build:watch
./.venv/bin/openai-vectorstore2-http
```

For separate Vite development, run:

```bash
npm run dev
```

The Vite dev server proxies `/api` and `/mcp` to `http://localhost:8000` by default.

## MCP development

The production HTTP app mounts authenticated MCP at `/mcp/`. For local FastMCP tooling, use the unauthenticated dev entrypoint:

```bash
./.venv/bin/fastmcp dev apps backend/app/mcp/dev_server.py:mcp --mcp-port 8001 --dev-port 8080 --no-reload
./.venv/bin/fastmcp dev inspector backend/app/mcp/dev_server.py:mcp --ui-port 6274 --server-port 6277 --no-reload
```

This entrypoint uses the same app services and `.env` settings as the backend, so local tool calls read and write the same development database and storage.

For a temporary ChatGPT developer-mode smoke test without OAuth, run the HTTP app with:

```bash
MCP_AUTH_MODE=none
```

Then expose the app over HTTPS and create the connector with the public `/mcp` URL. This maps MCP calls to the synthetic `local-dev` user and must not be used for production or shared data.

Authenticated ChatGPT Apps also need OAuth protected-resource metadata and an
authorization server that can issue MCP audience-bound tokens. Configure
`MCP_AUTHORIZATION_SERVERS=https://auth.example.com` only after that provider is
ready; unknown `/.well-known/*` paths intentionally return 404 instead of the
frontend shell.

When testing from ChatGPT's browser client, include ChatGPT origins in
`CORS_ORIGINS` so authenticated `/mcp/` preflight requests can reach the server:

```bash
CORS_ORIGINS=https://your-service.example,https://chatgpt.com,https://chat.openai.com
```

## Test commands

```bash
./.venv/bin/pytest
./.venv/bin/pyright
npm run typecheck
npm run build
npm run test:e2e -- --project=chromium-desktop
npm run test:e2e -- --project=chromium-mobile
```

Playwright uses live OpenAI and S3-compatible storage from `.env`, while Clerk is disabled through test-only overrides and local-dev auth is enabled.

## Future work

- Finish and document the private on-prem companion path in `vendor/openai-vectorstore2-on-prem`, including OpenAI-compatible local model serving, deployment notes, and the boundary between the base app and on-prem runtime
- Continue visual and interaction design work across Explorer, Library, Results, admin billing, previews, and mobile layouts
- Test the deployed MCP server from ChatGPT with a real user account, including tool discovery, authenticated calls, source previews, research actions, and MCP Apps UI rendering
- Add screenshots or a short walkthrough showing Explorer, Library search, ChatKit grounded answers, saved report artifacts, and MCP usage
- Expand browser coverage for realistic deployed flows, including Clerk auth, ChatKit citations, file reveal, research-library creation, and report persistence

## More docs

- [Architecture](docs/architecture.md)
- [Auth](docs/auth.md)
- [Admin Integration](docs/admin-integration.md)
- [Deployment](docs/deployment.md)
- [Security](docs/security.md)
- [Testing](docs/testing.md)
- [Migrations](docs/migrations.md)
- [Operations](docs/operations.md)

## Documentation note

This README was written with AI assistance in Codex, then manually reviewed and revised against the repository implementation.
