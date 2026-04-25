# Plan: Webapp and MCP Apps Exposure

## Planning Work Status

- [x] Analyze backend service, model, persistence, OpenAI gateway, ChatKit, and MCP entry points.
- [x] Analyze frontend ChatKit surface, REST API client, handwritten TypeScript contracts, and UI test opportunities.
- [x] Check current OpenAI docs for ChatKit custom backends and MCP Apps UI resource guidance.
- [x] Write this plan.
- [x] Review this plan against the repository findings.
- [ ] Implement the phases below.
- [ ] Keep this file updated as phases move from planned to complete.

## Goal

Expose the app capabilities in product-ready surfaces:

1. Webapp: a Vite/React app where all available functionality is reachable through an agentic ChatKit interface.
2. MCP: an MCP server exposing the same agentic capability set, plus MCP Apps UI resources.
3. Shared app-core capability boundary: the prompt says "three forms" but lists two external forms. This plan treats the third form as the internal, typed, reusable app-core interface that prevents web ChatKit, REST, and MCP from drifting. If a third external surface is intended, add it here before implementation.

The core capabilities to preserve and expose are PDF smart splitting, app-owned semantic chunk records, tag-filtered access to OpenAI vector stores, source inspection, search, branch search, QA, freeform generation, image generation, voice generation, and task/history visibility.

## Current Alignment

The repository already has the right general architecture. `README.md` describes an app-first semantic RAG workspace where the backend owns ingestion, splitting, tagging, storage, retrieval, and generation, with ChatKit and MCP as adapters over that app layer.

Backend:

- FastAPI creates services, mounts MCP at `/mcp`, serves REST APIs, serves the SPA, and exposes `/api/chatkit` at `backend/app/main.py`.
- `backend/app/bootstrap.py` wires a single service graph: database, auth, storage, OpenAI gateway, source service, action service, ChatKit store, and ChatKit server.
- App-owned SQLAlchemy models already include users, user libraries, tags, source files, source-tag links, semantic chunks, generated assets, tasks, ChatKit threads, ChatKit entries, and ChatKit attachments in `backend/app/models/records.py`.
- Pydantic app contracts live in `backend/app/schemas/records.py`.
- `SourceService` already handles upload, PDF/text/conversation extraction, OpenAI semantic splitting, auto-tag creation, chunk persistence, vector-store publication, tag-filtered vector-store search, and branch search in `backend/app/services/sources.py`.
- `ActionService` already runs QA, freeform, image, voice, task creation, task completion, and generated-asset persistence in `backend/app/services/actions.py`.
- `OpenAIGateway` centralizes OpenAI vector stores, files, Responses parsing, retrieval, image, voice, and transcription calls in `backend/app/integrations/openai_gateway.py`.

Webapp:

- The frontend is already Vite/React/TypeScript with `build:watch`, sourcemaps, and no minification in `package.json` and `frontend/vite.config.ts`.
- The app already has a library panel, upload, source selection, tags, search/branch, action buttons, result inspection, and a ChatKit panel in `frontend/src/App.tsx`.
- ChatKit is already mounted with `@openai/chatkit-react`, authenticated fetch, selected-source metadata injection, and `/api/chatkit` as the backend in `frontend/src/lib/api.ts`.
- Auth already supports Clerk or local-dev bearer mode via `frontend/src/main.tsx`, `backend/app/web_auth.py`, and `backend/app/services/auth.py`.

MCP:

- MCP is built with FastMCP in `backend/app/mcp/server.py`, mounted over streamable HTTP in `backend/app/main.py`, and exposed over stdio in `backend/app/mcp/stdio.py`.
- Current MCP tools cover sources, tags, source detail, text ingest, source delete, search, branch search, QA, freeform, image, voice, task list, and task detail.
- The existing MCP Apps UI is a Prefab "Semantic Sources" view registered from `backend/app/mcp/server.py`, and tests confirm a UI resource URI is advertised.

Tests:

- Current integration tests cover HTTP upload/search/QA and MCP tool discovery in `tests/integration/test_app_contracts.py`.
- `tests/conftest.py` already has a fake OpenAI gateway pattern that can support broader integration and browser tests without mocking every service boundary.

## Drift Risks To Fix Early

The project is already close enough that the largest risk is not missing code, but parallel concepts evolving separately:

- ORM models, Pydantic schemas, frontend TypeScript types, ChatKit tool payloads, and MCP tool payloads are hand-maintained separately.
- ChatKit tools and MCP tools expose similar actions under different names and slightly different parameter limits.
- Frontend source selection is passed to ChatKit as per-request metadata, while MCP callers pass source scope explicitly per tool.
- `AppTask` has `kind="ingest"`, but upload currently runs synchronously and returns `task=None`.
- Database bootstrapping uses `Base.metadata.create_all`; there is no migration path yet, so production schema changes could drift.
- OpenAI vector attributes are denormalized at ingest time with `tag_1` through `tag_8`. Tag edits, source title edits, or reindexing will require explicit vector-store synchronization.
- Source deletion removes app storage and DB records, but does not yet delete or detach OpenAI original files and chunk files.
- MCP has text ingest but not file/PDF ingest parity with the web upload endpoint.
- The current MCP Apps UI is useful but source-only; it is not yet the same agentic product surface as ChatKit.

## Phase 1: Stabilize App-Core Contracts

Status: completed for the initial implementation.

Define one typed app-core capability boundary that both ChatKit and MCP call. The goal is not a large abstraction layer; it is a small, typed facade over the existing services so the surfaces cannot drift.

Tasks:

- [x] Create or document a shared operation map for `list_sources`, `list_tags`, `get_source_detail`, `ingest_source`, `delete_source`, `search_chunks`, `branch_search`, `qa`, `freeform`, `generate_image`, `generate_voice`, `list_tasks`, and `get_task`.
- [x] Keep Pydantic request/response models in `backend/app/schemas/records.py` as the source of truth for app contracts.
- [x] Add a generated or checked TypeScript contract path. Options:
  - Generate TS types from Pydantic/OpenAPI during development.
  - Add a contract test that compares FastAPI OpenAPI schema names and key fields against `frontend/src/lib/types.ts`.
- [x] Normalize naming between ChatKit and MCP. Prefer user-facing tool names that read naturally but map to the same internal operation names.
- [x] Add a parity test that asserts ChatKit-exposed tools and MCP-exposed tools cover the same app-core operation set, allowing intentional differences such as render-only MCP UI tools.

Implementation notes:

- Added `backend/app/core/capabilities.py` as the shared operation matrix for app-core, REST, ChatKit, and MCP mappings.
- Added contract tests that check documented REST routes, MCP tool names/metadata, ChatKit tool names, and key frontend TypeScript fields against FastAPI OpenAPI schemas.
- Added `VectorstoreChatKitServer.tool_names()` as a public test/introspection hook.
- Updated `frontend/src/lib/types.ts` to include missing fields for chunk attributes, ingest finalize responses, task payloads, and task details.
- Updated the frontend `uploadSource` API helper to return the backend's actual `IngestFinalizeResponse` instead of pretending the upload endpoint returns a refreshed source list.
- Hid the `/mcp` redirect shim from OpenAPI schema generation so schema checks do not see transport plumbing as an app contract.

Acceptance criteria:

- One documented capability matrix exists.
- A test fails if a new app operation is added to one surface but not deliberately handled by the others.
- Frontend types have either generation or drift detection.

## Phase 2: Make Ingestion Task-Based And Reconciled

Status: completed for the in-process asyncio background execution baseline.

Move ingestion from a blocking request into app-owned task lifecycle state. Keep the current synchronous path initially if needed, but make the data model represent the real lifecycle.

Tasks:

- [x] Use `AppTask(kind="ingest")` for source upload and MCP ingest.
- [x] Return `IngestFinalizeResponse.task` from `/api/sources` instead of always returning `task=None`.
- [x] Track extraction, semantic split, vector-store upload, and final publication in `AppTask.state_json`.
- [x] Add clear source statuses: keep `processing`, `ready`, `failed`, and consider whether `queued` belongs on `SourceFile` or only on `AppTask`.
- [x] Make failures reconciled for tracked OpenAI files:
  - Preserve failed source records with useful `error_message`.
  - Track OpenAI original file IDs and chunk file IDs created before failure.
  - Add cleanup or reconciliation code for failed and deleted sources.
- [x] Add log lines for ingest start, split complete, vector attach complete, ingest complete, ingest failed, cleanup complete, and durations.

Implementation notes:

- Added ingest task creation to `SourceService.ingest_source` using existing `AppTask` fields, without adding schema yet.
- Web uploads now pass `origin_surface="web"` and MCP text ingest passes `origin_surface="mcp"`.
- Ingest state now records stages such as `stored_source`, `uploading_original_file`, `extracting_text`, `splitting_semantically`, `publishing_chunks`, `completed`, and `failed`.
- Integration tests now assert upload returns a completed ingest task and task history includes both ingest and QA tasks.
- Added OpenAI cleanup calls for source deletion: chunk files are detached from the vector store, tracked OpenAI files are deleted, and task `source_file_id` references are nulled before deleting the source row.
- Added failure-time cleanup for tracked OpenAI files when ingest fails after original or chunk files have been created.
- Integration tests now verify delete cleanup and failed-ingest cleanup through the fake OpenAI gateway.
- Added lifecycle logs for ingest start, split completion, chunk publication, ingest completion, ingest failure, and OpenAI cleanup.
- Added a lazy in-process asyncio ingest runner on `SourceService`, bounded by `task_runner_max_concurrency`.
- Upload and MCP file/text ingest now return after creating a source plus `AppTask(status="queued")`; the background worker moves the task through `running`, `completed`, `failed`, or `cancelled`.
- The worker reopens fresh DB sessions, reads the stored source payload from app storage, and reconciles the same extraction, semantic split, OpenAI upload, vector publication, source status, task result, and failure cleanup state as the previous inline path.
- `AppServices.close()` now stops source background tasks before closing OpenAI/auth/database clients.
- Integration tests now poll task detail until completion/failure before asserting search, QA, source detail, MCP ingest behavior, and cleanup.
- Source statuses remain `processing`, `ready`, and `failed`; queue state lives on `AppTask`.
- Remaining Phase 2 hardening: durable queue/restart recovery, explicit delete-vs-running-ingest cancellation semantics, and optional user-facing retry/reconcile commands.

Decision updates:

- Background ingestion will use an in-process asyncio worker first, bounded by `task_runner_max_concurrency`.
- Queue state should live on `AppTask`; source records can continue using `processing`, `ready`, and `failed`.

Acceptance criteria:

- Uploading a source creates an ingest task visible through `/api/tasks`.
- The UI can show an upload as queued/running/failed/ready without guessing.
- A failed ingest does not leave untracked OpenAI vector-store files.

## Phase 3: First-Class PDF Smart Split

Status: completed for the current web, ChatKit, REST, MCP, and MCP Apps baseline.

PDF smart split already exists as a hidden path inside upload. Make it first-class so the app can preview, tune, re-run, and expose it through ChatKit and MCP.

Tasks:

- [x] Extract PDF text by page into a typed intermediate structure instead of one large string. Preserve page numbers and source offsets.
- [x] Use `semantic_split_pdf_batch_pages` from `AppSettings`; it currently exists but is not used.
- [x] Add a page-batched smart split strategy that can handle large PDFs without overflowing model input.
- [x] Add a split-preview model that includes proposed chunks, titles, summaries, keywords, locators, and auto-tags before vector-store publication.
- [x] Decide whether split preview is persisted as task `state_json`, a new table, or only a transient response. Prefer task state first unless preview editing becomes complex.
- [x] Add a re-split operation for a failed or ready source that invalidates/replaces chunks and vector-store files safely.
- Expose PDF smart split through:
  - [x] Web ChatKit tool.
  - [x] REST endpoint for upload/preview/finalize if needed by the UI.
  - [x] MCP file/PDF ingest tool.
  - [x] MCP Apps UI controls.

Implementation notes:

- Added `PdfTextBatch` and `build_pdf_text_batches()` to preserve page markers and group extracted PDF text by `semantic_split_pdf_batch_pages`.
- `SourceService` now routes PDFs through a page-batched semantic split path when more than one batch is needed.
- Added focused parser tests for page batch labels, page marker preservation, and fallback behavior when extracted text has no page markers.
- Added MCP `ingest_file_source` as a base64 file/PDF ingest route over the same app-core ingestion operation.
- Added transient `SplitPreviewResponse` contracts and `SourceService.preview_semantic_split()` for inspect-only previews over raw file/text payloads.
- Added `POST /api/sources/split-preview`, ChatKit `preview_semantic_split`, MCP `preview_text_split`, and MCP `preview_file_split`. These run extraction and semantic/PDF batching but do not create sources, tasks, chunks, OpenAI files, or vector-store attachments.
- Added frontend TypeScript/API contracts for split previews so a UI can call the REST preview path later.
- Added integration tests that prove REST and MCP previews return chunk/tag drafts while leaving source/task state empty.
- Added a task-backed `resplit_source` operation exposed through REST, ChatKit, and MCP. It computes the new split before deleting old chunk/vector files so pre-replacement failures preserve existing ready chunks.
- Re-split preserves the original OpenAI file when present, replaces old semantic chunk rows after successful split, detaches/deletes old chunk vector files, and records replacement progress in `AppTask(kind="resplit")`.
- Added frontend TypeScript/API contracts for re-split and integration tests for successful replacement plus failed pre-replacement preservation.
- Added direct webapp controls to stage selected files, run inspect-only split preview before upload, enqueue upload, and queue a safe re-split for the selected source.
- Added MCP Apps UI controls to inspect a source from the rendered source browser and queue a re-split from the app UI.
- Added a generated two-page PDF fixture test that exercises real PDF extraction, page markers, and page-range batching.
- Remaining Phase 3 work: none for the current baseline. Future polish can add editable preview history, richer per-page offsets, and native host file inputs where available.

Decision updates:

- Split preview should be inspect-only. Users iterate by asking the agent to adjust guidance and re-run the split, rather than manually editing chunk drafts.
- Split preview is transient for now, not persisted in task state or a preview table.

Acceptance criteria:

- Large PDFs split in batches with correct page-range locators.
- The same split result shape is used by web, ChatKit, and MCP.
- Tests cover PDF extraction and chunk locator behavior with a small fixture PDF.

## Phase 4: Tag Model And Vector-Store Filter Correctness

Status: planned.

Tag-filtered access already works through vector attributes. Harden it so tag edits and app metadata remain synchronized with OpenAI vector-store filters.

Tasks:

- Add manual tag create/update/delete operations if users need editable tags.
- Decide whether tags attach only to sources or can also attach to chunks. Current model attaches tags to sources.
- Keep `TAG_SLOT_COUNT=8` as an explicit product limitation or redesign attributes for more tags per source if OpenAI vector-store filtering supports the needed shape.
- Add reindexing when tags change, because vector attributes are currently written once at chunk publication.
- Add a `vector_attributes_version` concept, either in chunk metadata or in settings, so future reindexing can detect stale chunks.
- Add DB post-filtering fallback only if OpenAI vector-store filters cannot express a future tag model.
- Add tests for all/any tag matching and source-kind/source-id combinations.

Acceptance criteria:

- Tag-filtered search returns the same logical result set from REST, ChatKit, and MCP.
- Reindexing is deterministic and observable through logs/tasks.
- Tag changes cannot silently leave stale vector-store filters.

## Phase 5: Agentic ChatKit Webapp

Status: planned.

Make ChatKit the main way to access the app, with the surrounding UI acting as context, inspection, and control surface.

Tasks:

- Keep the current custom ChatKit backend pattern. Official docs describe this as implementing `ChatKitServer`, persisting threads/messages/files with a `Store`, forwarding requests to the server, and passing custom context into `server.process`.
- Move selected source scope from only request metadata toward explicit thread/app state:
  - Continue sending current selections from the frontend.
  - Store meaningful scope decisions in thread metadata or app task input when the agent acts.
  - Make the scope visible in the UI and in task history.
- Add ChatKit widgets or structured outputs for:
  - Search hits with citations and locators.
  - Branch search levels.
  - Source detail and chunk map.
  - Generated asset links/previews.
  - Ingest progress and split preview.
- Decide whether ChatKit attachments should become source ingestion inputs. The backend has ChatKit attachment storage, while the composer currently disables attachments.
- Replace the standalone action buttons with agent-friendly controls where useful, but keep direct controls for repeatable workflows like upload, source selection, and filter editing.
- Add helpful progress events for all long-running tools, not only search/branch/image.
- Tighten layout and styling after functionality is stable. The current UI works, but it uses a hero-like layout and broad cards; for an operational RAG workspace, move toward denser, quieter controls.

Acceptance criteria:

- A user can upload or select sources, ask ChatKit to search/branch/answer/generate, and inspect cited chunks without leaving the agentic flow.
- ChatKit history persists and reloads across sessions.
- ChatKit actions create tasks with `origin_surface="chatkit"` and correct `origin_thread_id`.

## Phase 6: MCP Server And MCP Apps UI

Status: planned.

Turn the MCP server from a useful tool adapter into a ChatGPT Apps-ready surface with data tools, render tools, auth metadata, and file/PDF parity.

Tasks:

- Preserve the current streamable HTTP and stdio entry points.
- Add ChatGPT Apps-ready auth:
  - Serve protected-resource metadata for `/mcp/`.
  - Publish or integrate the required authorization-server metadata for the chosen Clerk/OAuth setup.
  - Add security schemes and scope checks to MCP tool descriptors.
  - Verify audience/resource/scopes consistently.
  - Document HTTPS and `APP_BASE_URL` requirements.
- Split data tools from render tools, following current OpenAI Apps guidance:
  - Data tools return JSON only.
  - Render tools attach `_meta.ui.resourceUri` and, for ChatGPT compatibility, `_meta["openai/outputTemplate"]` when needed.
- Expand MCP Apps UI beyond the current sources browser:
  - Library browser.
  - Source detail and chunk inspection.
  - Search results and branch levels.
  - Split preview and ingest progress.
  - Task history and generated assets.
- Add file/PDF ingest parity:
  - Support ChatGPT Apps file inputs where available.
  - Keep `ingest_text_source` for simple text.
  - Add `ingest_file_source` or `ingest_pdf_source` that reaches the same app-core ingestion operation as web upload.
- Decide whether to expose an MCP-level agent tool that wraps the same ChatKit/Agents SDK behavior, or keep MCP as tool primitives for the host model. Prefer primitives plus render tools first, then add a higher-level `ask_library_agent` only if real hosts need it.
- Consider adding canonical `search` and `fetch` tools for Company Knowledge/deep research compatibility while preserving app-specific `search_chunks`.

Acceptance criteria:

- MCP hosts can use the same capabilities as the webapp, including PDF smart split and tag-filtered retrieval.
- MCP Apps UI resources use `text/html;profile=mcp-app` and versioned `ui://` URIs.
- Tool descriptors advertise correct read-only/destructive hints, security schemes, and UI resource metadata.
- Tests verify tool discovery, metadata, auth challenge, UI resource MIME, and data/render split.

Implementation notes:

- Added MCP `ingest_file_source` with `filename`, `payload_base64`, optional `media_type`, tags, and guidance. It routes through `SourceService.ingest_source` with `origin_surface="mcp"`.
- Updated the capability matrix so one app-core operation can map to multiple MCP tools.
- Contract tests now require `ingest_file_source` to be advertised with `filename` and `payload_base64`, and a behavioral MCP test verifies it creates a ready source plus an MCP-origin ingest task.
- Remaining MCP file work: native Apps file params, UI controls, auth metadata, and data/render split.

Decision updates:

- Production Apps/MCP auth should commit to Clerk as the OAuth/linking provider rather than staying generic.

## Phase 7: Data Model And Migration Discipline

Status: planned.

The backend tables are "probably setup fine" for local development, but production needs migration discipline before more tables and fields are added.

Tasks:

- Add Alembic or an equivalent migration path before changing schema heavily.
- Create an initial migration from the current ORM state.
- Add migrations for:
  - Ingest task state refinements.
  - Any split preview persistence.
  - Vector attribute versioning or reindex metadata.
  - OAuth/client/linking state if Clerk does not own it fully.
  - Any app-core operation audit tables, if needed.
- Add a schema check in CI that detects model drift from migrations.
- Keep ChatKit tables and app-core tables intentionally related:
  - `AppChatThread` and `AppTask.origin_thread_id`.
  - `AppChatAttachment` and source ingestion, if attachments become sources.
  - Generated assets and tasks.

Acceptance criteria:

- New database state is introduced through migrations, not only `create_all`.
- The relationship between ChatKit state and app-core state is documented and tested.

## Phase 8: Automated Tests And Codex-Driven UI Checks

Status: planned.

Keep the integration-test preference. Add browser automation only where it verifies real user workflows and layout behavior.

Backend integration tests:

- Expand `tests/integration/test_app_contracts.py` to cover tag filters, branch search, task lifecycle, failure state, delete cleanup, and MCP parity.
- Add tests for PDF smart split with a fixture PDF.
- Add tests for OpenAI vector-store filter construction, especially all/any tags and source/source-kind combinations.

Frontend and browser tests:

- Add Playwright.
- Run the FastAPI app with the fake OpenAI gateway and built frontend.
- Test upload, source selection, search, QA, generated asset display, and task visibility.
- Add ChatKit smoke tests for mount, authenticated `/api/chatkit` fetch, selected-source metadata, streamed response rendering, and history load.
- Add screenshot checks at desktop, 1280px, 820px, and mobile breakpoints for:
  - Three-column layout.
  - ChatKit panel placement.
  - No text clipping.
  - No overlapping controls.
  - Results and source cards not resizing unexpectedly.

Codex-driven UI tests:

- Add a documented command that starts the app in test mode and lets Codex or Playwright capture screenshots.
- Store screenshots as artifacts rather than committing generated screenshots by default.
- Consider a small "UI smoke skill" later if repeated browser verification becomes common.

Acceptance criteria:

- `./.venv/bin/pytest`, `./.venv/bin/pyright`, `npm run typecheck`, and a Playwright smoke suite cover the main surfaces.
- Browser tests can run with local-dev auth and fake OpenAI behavior.

## Phase 9: Documentation And Operational Readiness

Status: planned.

Update docs as the implementation catches up.

Tasks:

- Update `README.md` so it distinguishes current local capabilities from ChatGPT Apps production requirements.
- Add a `docs/` directory with:
  - Architecture and capability matrix.
  - Data model and migration policy.
  - ChatKit webapp behavior.
  - MCP server and Apps UI behavior.
  - Auth setup for local-dev, Clerk web, MCP HTTP, stdio, and ChatGPT Apps.
  - Testing and screenshot workflow.
- Add `.env.example` entries for any new OAuth, MCP Apps, or ChatKit settings.
- Add operational notes for OpenAI file/vector-store cleanup and reindexing.

Acceptance criteria:

- A new contributor can run the webapp, run MCP locally, understand required production auth, and run tests from docs alone.

## Suggested Order

1. Stabilize app-core contracts and drift checks.
2. Add migration discipline before schema changes.
3. Convert ingestion into task-based lifecycle state.
4. Make PDF smart split first-class and batch-aware.
5. Harden tag/vector-store reindexing.
6. Upgrade ChatKit webapp into the primary agentic surface.
7. Bring MCP and MCP Apps UI to parity.
8. Add Playwright and screenshot-driven UI checks.
9. Update docs and deployment guidance.

## Open Questions

- Resolved: PDF split preview is inspect-only; users should iterate by asking the agent to adjust/re-run splitting guidance.
- Resolved: background ingestion should use an in-process asyncio worker first.
- Resolved: production MCP/Apps auth should commit to Clerk.
- Still open: Is the intended third external form something other than the shared app-core capability boundary?
- Still open: Should ChatKit attachments become source ingestion inputs, or should uploads stay outside chat?
- Still open: Should MCP expose a high-level `ask_library_agent` tool, or should host models compose primitive tools?
- Still open: Do tags remain source-level only, or do chunk-level tags matter?
- Still open: Is the eight-tag vector attribute limit acceptable for the first production version?

## Official References Checked

- ChatKit custom backend: https://developers.openai.com/api/docs/guides/custom-chatkit
- ChatKit frontend embedding: https://developers.openai.com/api/docs/guides/chatkit
- MCP Apps resource templates and MIME type: https://developers.openai.com/apps-sdk/build/mcp-server#step-1--register-a-component-template
- MCP Apps data-tool/render-tool split: https://developers.openai.com/apps-sdk/build/chatgpt-ui#decoupled-pattern
- MCP Apps tool descriptor metadata: https://developers.openai.com/apps-sdk/reference#_meta-fields-on-tool-descriptor
- MCP Apps state management: https://developers.openai.com/apps-sdk/build/state-management#summary
