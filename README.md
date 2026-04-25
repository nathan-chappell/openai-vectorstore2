# OpenAI Vectorstore2

OpenAI Vectorstore2 is an app-first semantic RAG workspace. The backend owns ingestion, semantic splitting, tagging, storage, retrieval, and generation workflows. ChatKit is the primary frontend surface, while MCP exposes the same app functionality to MCP hosts and MCP Apps UIs.

## Shape

- Backend: FastAPI, async SQLAlchemy, pydantic-settings, OpenAI Responses/vector stores, ChatKit server, FastMCP.
- Frontend: Vite, React, TypeScript, Clerk, ChatKit.
- Storage: local file storage by default, with an S3-compatible adapter for deployment.
- Retrieval: semantic chunks are stored as app records and uploaded as individual OpenAI vector-store files. Retrieval resolves vector hits back to full app-owned chunks before model calls.

## Core Workflows

- Upload PDFs, text files, and audio/video conversation recordings.
- Extract text/transcripts, semantically split into chunks, auto-tag, and publish chunks to OpenAI vector stores.
- Search with source, kind, and tag filters.
- Run QA, free-form generation, image generation, voice generation, and branch search over semantic chunks.
- Use ChatKit as the main UI and MCP as an adapter over the same service layer.

## Local Development

1. Create `.env` from `.env.example`.
2. Install Python dependencies into `.venv`.
3. Run `npm install`.
4. Run `npm run build:watch`.
5. Start the backend with `./.venv/bin/openai-vectorstore2-http`.
6. Open `http://localhost:8000`.
7. Point MCP hosts at `http://localhost:8000/mcp/`.

## Verification

- `./.venv/bin/pytest`
- `./.venv/bin/pyright`
- `npm run typecheck`
- `npm run build`
