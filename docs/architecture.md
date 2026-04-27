# Architecture

OpenAI Vectorstore2 is organized around an app-core service layer. REST, ChatKit, and MCP call the same source/action services so ingestion, retrieval, tasks, and cleanup do not drift between surfaces.

## Core Boundary

- `backend/app/services/sources.py` owns source upload, source-level OpenAI vector-store indexing, tag assignment, search, branch search, optional semantic split tooling, tag/path reindexing, and source cleanup.
- `backend/app/services/actions.py` owns QA, freeform generation, image generation, voice generation, generated assets, and action tasks.
- `backend/app/core/capabilities.py` records the intended operation map across REST, ChatKit, and MCP.
- `backend/app/schemas/records.py` is the Pydantic contract source for API responses and app task payloads.

## Data Model

The ORM models live in `backend/app/models/records.py`.

- App-core tables: users, libraries, sources with one representative tag slug, optional semantic chunks, tasks, billing records, payment/free-credit requests, and generated assets.
- ChatKit tables: threads, entries, and attachments.
- The important linkage points are `AppTask.origin_thread_id`, `AppChatThread.metadata_json.selected_source_ids`, and `AppChatAttachment.payload.metadata.source_id/task_id`.

## Webapp

The web UI is a Vite/React app served by FastAPI after build.

- The file workspace is split into Explorer, Library, and Results views. Explorer handles folder navigation, Library handles semantic search plus one tag filter, and Results aggregates ChatKit/tool references.
- Row click opens preview; ChatKit retrieval scope comes from explicit `@` file references, tool-provided source IDs, or current search/reveal actions rather than a persistent checkbox selection.
- ChatKit composer attachments are disabled in the current UX. The backend attachment endpoint remains compatibility plumbing and still turns uploads into normal app-core sources if used by an older host.
- Background ingest, re-split, and reindex tasks are polled while active so file readiness updates without manual refresh.

## ChatKit

`backend/app/chatkit/server.py` implements the custom ChatKit backend.

- ChatKit receives explicit source IDs and entity references through request/tool metadata.
- Tools map directly to app-core operations: list/inspect sources, manage the representative source tag, preview split, ingest text, re-split, search, branch, QA, freeform, image, voice, list tasks, and inspect tasks.
- The frontend composer lists common app tools such as research build, library search, grounded answer, split preview, and report saving; ChatKit forwards a selected composer tool as `inference_options.tool_choice`, and the server validates it against registered tools before forcing that tool choice on the agent run.
- Long-running ChatKit tools emit progress updates with useful counts, task IDs, and generated asset IDs.

## Reports

Structured report documents live in `backend/app/schemas/reports.py` and render through `backend/app/services/reports.py`.

- `POST /api/reports/markdown` renders a structured report to Markdown and saves the result through the canonical source ingestion path.
- Saved Markdown reports are normal library sources with report metadata, so they can be searched, selected for ChatKit context, downloaded, deleted, and cited like uploaded files.
- PDF rendering and inspection are planned as a later export path over the same structured report source.

## MCP And Apps UI

`backend/app/mcp/server.py` exposes the same app-core capabilities through FastMCP.

- Data tools return structured JSON for hosts.
- MCP Apps render tools are split by capability: `sources` for file browsing/detail, `source_search` for retrieval, `research_libraries` for research build/candidate review, and `activity` for recent task state.
- HTTP MCP is mounted at `/mcp`; stdio is available through `openai-vectorstore2-stdio`.
- Local FastMCP Apps and Inspector workflows use `backend/app/mcp/dev_server.py:mcp`, which builds the same services with the dev MCP server factory and no production token verifier.

## Vector Store Metadata

OpenAI vector-store attributes are denormalized on each indexed source file. Semantic split records may exist for inspection, but normal ingestion does not publish split chunks to the vector store.

- `attributes_version=3`
- `index_kind=source_file`
- `source_id`, `source_kind`, `virtual_path`, `virtual_name`
- numeric `created_at`
- scalar `tag` containing the source's representative tag slug when present

Each source currently has at most one representative alphanumeric-hyphenated tag. The model should prefer an existing broad tag when it fits, otherwise create one concise reusable slug.
