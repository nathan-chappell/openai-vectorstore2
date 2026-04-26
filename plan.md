# Plan: AI File Browser, ChatKit, And MCP Apps

## Goal

Expose the app's core capabilities in product-ready surfaces:

1. Webapp: a Vite/React app where users browse an AI-powered virtual filesystem and drive the library through a ChatKit agent.
2. MCP: an MCP server exposing the same capability set, plus MCP Apps UI resources.
3. Shared app-core boundary: the prompt originally said "three forms" but listed two external forms. This plan treats the third form as the typed app-core capability layer that keeps REST, ChatKit, MCP, database models, and frontend contracts from drifting.

The capabilities to preserve and expose are PDF smart split, app-owned semantic chunks, tag-filtered OpenAI vector-store access, virtual folders/paths, source inspection, search, branch search, grounded QA, freeform generation, generated assets, task/history visibility, and agent-mediated file selection.

## Status Snapshot

- [x] Analyze backend service, model, persistence, OpenAI gateway, ChatKit, MCP, frontend, and UI test entry points.
- [x] Build the current baseline phases: migrations, task-based ingestion, PDF smart split, tag/vector-store reindexing, ChatKit tools, MCP tools/UI, Playwright, docs, and VS Code launch/build-watch support.
- [x] Rebuild the web product around a compact virtual filesystem explorer plus ChatKit.
- [x] Add a DB-backed virtual filesystem layer with folders, paths, rename/move/delete, selected-file scope, and OpenAI file-input preparation for ChatKit.
- [x] Promote and retain the new Research Importer feature plan as the next planned product feature.
- [x] Add the first Research Importer backend capability for fast library seeding and reviewed reference ingestion.
- [x] Apply the first browser/design fixes: hide empty preview, widen preview-on-selection, and add a persisted draggable explorer/chat splitter.
- [ ] Continue tightening explorer UX, ChatKit coordination, and Playwright coverage around real file-browser workflows.

## Checkpoint: 2026-04-26 Wrap-Up

Status: implementation checkpoint committed for handoff.

Completed in the current checkpoint:

- Added the Research Importer backend foundation: database model/migration, source provenance metadata, Pydantic schemas, settings, OpenAI web-search discovery gateway, `ResearchImportService`, REST routes, ChatKit tools, MCP tools, capability-matrix entries, frontend API/types, and integration/contract coverage.
- Kept canonical ingestion through `SourceService.ingest_source`; imported seeds/candidates create normal source records, OpenAI files, vector-store files, tasks, and metadata rather than using a parallel ingest path.
- Added reviewed candidate state transitions for pending, approved, rejected, ingested, and failed candidates.
- Implemented the first explorer layout fixes: the preview pane is hidden until a file is selected, selected-file preview gets more room, folder navigation clears stale preview state, and the explorer/chat split is draggable and persisted in `localStorage`.
- Updated `plan.md` to preserve the Research Importer feature plan and browser/design queue.

Verification completed before pausing:

- `npm run typecheck`
- `./.venv/bin/pyright`
- `./.venv/bin/pytest tests/test_migrations.py tests/integration/test_app_contracts.py -q`

Known remaining work for the next session:

- Run `npm run build` after this checkpoint.
- Refresh the Playwright workspace shell assertions for the hidden-preview default state.
- Add/adjust Playwright coverage for the new splitter and preview-visible-on-selection behavior.
- Add focused parser/normalizer tests for HTML cleanup, PDF/arXiv resolution, exported LinkedIn cleanup, URL normalization, and dedupe behavior.
- Build the web Research Import review panel after the browser/file-explorer direction settles.
- Consider whether `.gitignore` should use `tmp/` instead of `./tmp` and whether `tmp/image.png` should remain committed.

## Current Product Shape

The product is now centered on an AI-powered file explorer:

- The left surface behaves like a normal, compact file explorer with folders, files, query, tags, upload, rename, move, delete, preview, and selected-file scope.
- The preview pane should stay hidden until a file is selected. Once a file is selected, the preview should open wide enough to be useful for the selected media/text/PDF rather than remaining a cramped placeholder column.
- ChatKit sits beside the explorer and uses selected ready files as OpenAI file inputs. Composer attachments stay hidden in the current web UX.
- The horizontal split between the file explorer/preview side and ChatKit should be user-resizable with slider-like drag behavior, so users can allocate more width to browsing/previewing or to chat as needed.
- OpenAI vector stores remain the semantic retrieval and tag-filtered search layer.
- The app database owns the virtual filesystem, task lifecycle, tags, provenance, ChatKit tables, source/chunk records, generated assets, and all relationships needed to prevent data-model drift.
- MCP exposes the same app-core capabilities as tools and Apps UI resources.

## Current Architecture Alignment

Backend:

- FastAPI wires services, REST, ChatKit, MCP, static frontend serving, and auth.
- `SourceService` owns ingest, PDF/text extraction, semantic splitting, OpenAI file/vector-store publication, tags, virtual filesystem operations, selected-file preparation, reindexing, and cleanup.
- `ActionService` owns grounded QA, freeform generation, generated assets, and task lifecycle for user-facing actions.
- `OpenAIGateway` centralizes OpenAI vector stores, files, Responses parsing, retrieval, image, voice, and transcription calls.
- Alembic migrations and drift tests now guard schema changes.

Frontend:

- Vite/React/TypeScript is in place with `npm run build:watch`, source maps, and production minification disabled until desired.
- The main app is now an explorer-plus-ChatKit workspace.
- Playwright covers workspace shell behavior and selected-file ChatKit flows.
- Follow-up: remove unused legacy React components in `frontend/src/App.tsx` once the virtual explorer stabilizes further.

MCP:

- FastMCP exposes streamable HTTP and stdio entry points.
- Current tools cover source/file operations, tags, search, branch search, split preview, re-split, QA, freeform, image, voice, task visibility, and virtual filesystem operations.
- MCP Apps UI resources expose the library/explorer query surface.

Tests and operations:

- Backend integration tests cover contracts, migrations, vector attributes, ingest/search/QA, cleanup, and MCP discovery.
- Playwright can run with Clerk disabled and local-dev auth.
- Local live checks can use OpenAI and Railway/S3-compatible storage values from ignored `.env`.

## Next Planned Feature: Research Importer For Fast Library Seeding

Status: planned and ready to implement as the next capability track.

Intent:

Add a persistent in-app research importer that can quickly seed a user's library from a small starting point, then discover and queue related references for review. The first supported flow should prioritize public URLs, PDFs/arXiv-style links, pasted text, uploaded files, and manually exported LinkedIn article content. Do not build authenticated LinkedIn scraping in v1; login-gated scraping would be brittle and should not be bypassed.

Decisions retained from the added feature plan:

- Build this as an app backend feature, not a one-off Codex skill or hosted-container workflow.
- Keep `SourceService.ingest_source` as the canonical path for creating sources, chunks, tags, tasks, OpenAI files, and vector-store files.
- Use OpenAI `web_search` for discovery, query expansion, and cited candidate finding.
- Keep fetching, normalization, dedupe, approval state, ingestion, and provenance in this backend.
- Use a review queue before reference ingestion. The initial seed may be ingested directly; discovered references should stay pending until approved.
- Bound default expansion: max depth 2, max 8 candidates per source, and max 40 pending candidates per import task unless settings are changed.
- Defer deep-research/report-first flows to a later enhancement. They can eventually produce a curated bibliography that feeds the same importer.

Implementation outline:

- Add a `ResearchImportService` that accepts seeds as public URL, PDF/arXiv URL, uploaded file, pasted text, or exported LinkedIn HTML/text.
- Add persistent candidate records with URL, title, source type, depth, parent candidate/source, rationale, score, status, linked source ID, provenance metadata, and content hash.
- Add provenance metadata to source records, likely through a `SourceFile.metadata_json` column, including original URL, DOI/arXiv ID when known, import task/candidate IDs, parent source, content hash, and fetch timestamp.
- Add fetch/normalize helpers:
  - PDFs store original PDF bytes.
  - HTML pages become cleaned Markdown or plain text.
  - arXiv abstract URLs resolve to PDFs when possible.
  - LinkedIn imports accept pasted/exported content only.
  - Paywalls, login walls, and anti-bot gates are not bypassed.
- Add REST endpoints to create an import task, read task candidates, approve/reject candidates, and ingest approved candidates.
- Expose matching ChatKit and MCP tools for starting imports, listing candidates, approving/rejecting candidates, and ingesting approved candidates.
- Add a web Research Import panel or explorer action with seed input, bounded depth/candidate controls, task progress, and candidate approve/reject/ingest actions.
- Update `backend/app/core/capabilities.py`, Pydantic schemas, frontend TypeScript contracts, task kind unions, migrations, and docs to include `research_import`.

Test plan:

- Integration test that a pasted/text seed with explicit URLs creates a `research_import` task, ingests the seed when requested, and leaves references as pending candidates.
- Integration test that approving candidates and ingesting approved items creates normal source records through `SourceService.ingest_source`.
- Unit tests for URL/PDF/arXiv/HTML normalization, dedupe/content hash behavior, and LinkedIn exported HTML cleanup.
- Contract tests for REST schemas, frontend types, ChatKit/MCP tool names, capability matrix, and migrations.
- Existing ingest, search, QA, vector attribute, PDF batch, MCP, and Playwright checks should continue to pass.

## Research Capability Implementation Track

Status: not started.

Phase 1: schema, contracts, and drift guards.

- [ ] Add Alembic migration and ORM records for research import candidates, with fields for library, owner/task, parent source/candidate, URL, title, source type, depth, rationale, score, status, linked source, provenance metadata, content hash, and timestamps.
- [ ] Add source provenance storage, likely `SourceFile.metadata_json`, for original URL, normalized URL, DOI/arXiv ID when known, import task/candidate IDs, parent source, content hash, fetch timestamp, and importer version.
- [ ] Add Pydantic request/response models for create import task, list candidates, update candidate status, and ingest approved candidates.
- [ ] Extend task kind unions, frontend TypeScript contracts, `backend/app/core/capabilities.py`, and MCP/ChatKit operation names with `research_import`.
- [ ] Add migration drift and contract tests before wiring UI.

Phase 2: seed ingestion, normalization, and dedupe.

- [ ] Implement `ResearchImportService` with pasted text, public URL, PDF/arXiv URL, uploaded file, and exported LinkedIn HTML/text seed inputs.
- [ ] Normalize URLs and content, compute content hashes, and avoid duplicate pending candidates or duplicate ingested sources inside a library.
- [ ] Route every approved or directly ingested seed through `SourceService.ingest_source`; do not create a parallel ingest path.
- [ ] Keep login walls, paywalls, and anti-bot gates as explicit failed/degraded candidate states rather than bypassing them.
- [ ] Add focused parser/normalizer tests for HTML cleanup, PDF/arXiv resolution, exported LinkedIn cleanup, URL normalization, and dedupe behavior.

Phase 3: OpenAI discovery and candidate review.

- [ ] Use OpenAI `web_search` to expand seed queries and discover cited/related candidate URLs with rationale and score.
- [ ] Enforce default bounds: max depth 2, max 8 candidates per source, and max 40 pending candidates per import task.
- [ ] Persist candidates as pending records and expose approve/reject/ingest transitions.
- [ ] Record task state for discovery progress, candidate counts, degraded fetches, and ingestion results.
- [ ] Add integration tests for seed-to-pending-candidates and approved-candidates-to-normal-sources.

Phase 4: surfaces.

- [ ] Add REST routes for starting imports, reading candidates, approving/rejecting candidates, and ingesting approved candidates.
- [ ] Add ChatKit tools so the agent can start an import, inspect candidates, approve/reject items, ingest approved items, and report progress.
- [ ] Add MCP tools with the same app-core operation mapping.
- [ ] Add a compact web Research Import action/panel near explorer upload, with seed input, bounded controls, task progress, and candidate review.
- [ ] Add Playwright coverage for the browser review flow once fake or deterministic live discovery is available.

Phase 5: documentation and operations.

- [ ] Document importer limits, provenance, non-bypass policy for gated content, and expected user review workflow.
- [ ] Add operational notes for cleanup/retry of failed import tasks and stale pending candidates.
- [ ] Re-run `./.venv/bin/pyright`, focused backend tests, `npm run typecheck`, `npm run build`, and targeted Playwright checks.

## Browser And Design Fix Queue

Status: active intake; implement items as concrete plans arrive.

- [ ] Hide the preview surface until a file is selected so empty browsing starts as a clean explorer plus ChatKit layout.
- [ ] When a file is selected, open a preview area wide enough for the selected media/text/PDF instead of leaving a narrow placeholder.
- [ ] Add a draggable horizontal splitter between the explorer/preview region and ChatKit, with sensible min/max widths and persisted preference.
- [ ] Keep the file explorer dense, fast, and familiar: normal folder/file rows, tight metadata, simple affordances, no marketing-style cards or explanatory copy.
- [ ] Validate browser/design fixes with Playwright desktop and mobile screenshots before marking complete.
- [ ] Add future browser/design plans here first, then move them into implementation tasks as soon as enough detail exists.

## Near-Term Follow-Ups

- Implement Research Importer as the next substantial feature track.
- Work through the Browser And Design Fix Queue as new instructions arrive.
- Add focused Playwright flows for normal file-browser behavior: create folder, upload text/json, select files, ask ChatKit a grounded question, reveal/go-to-location, rename/move, and delete.
- Add backend integration coverage for recursive folder delete, move/rename reindexing, selected-file preparation, and ChatKit client-tool effects.
- Decide whether ChatKit should eventually expose richer structured widgets for source detail, search hits, task progress, and import candidates.
- Remove unused legacy frontend components and any stale CSS once the new explorer has fully replaced the old workbench paths.
- Revisit vector-store publishing retry/backoff if real concurrent ingestion load increases beyond the current single-runner default.

## Completed Implementation History

### Virtual Filesystem Explorer Rebuild

Status: completed.

Implemented:

- New `filesystem_entry` ORM table, Alembic migration, root/folder/source backfill, and source path fields.
- REST APIs for listing folders, searching filesystem entries, creating folders, renaming/moving entries, recursive permanent deletes, and current-folder ingest.
- Vector attributes v3 with `virtual_path` and `virtual_name` while staying inside OpenAI's 16-key metadata limit.
- Selected-file OpenAI file-input preparation for ChatKit, including re-uploading legacy originals as `user_data` when needed.
- ChatKit tools for filesystem listing/search/mutation and client-tool coordination: `set_file_selection`, `reveal_file`, and `set_file_search`.
- Matching MCP tools and capability matrix entries for filesystem operations.
- Compact web explorer with breadcrumbs, rows, query, tags, create folder, rename, delete, drag-to-folder move, upload, preview, and ChatKit selection scope.

Verification completed:

- `npm run typecheck`
- `npm run build`
- `./.venv/bin/pyright`
- `./.venv/bin/pytest tests/test_migrations.py tests/test_vector_attributes.py tests/integration/test_app_contracts.py -q`
- `npm run test:e2e -- --grep "workspace shell"`
- `npm run test:e2e -- --project=chromium-desktop --grep "explorer-selected"`

### Earlier Completed Phases

Status: completed for the current baseline.

- App-core contracts and drift checks: added a shared capability matrix and contract tests across REST, ChatKit, MCP, and frontend TypeScript fields.
- Task-based ingestion: uploads and MCP ingests create `AppTask` rows, run through the in-process asyncio worker, track stage state, and reconcile failures/cleanup.
- PDF smart split: added page-batched PDF splitting, split preview, re-split, REST/ChatKit/MCP exposure, and parser/PDF fixture tests.
- Tag model and vector filters: kept tags source-scoped, added bounded OpenAI metadata slots, date filters, vector attribute versions, tag update/reindex, and DB post-filtering.
- ChatKit webapp: expanded app tools, persisted selected-file scope in thread metadata, hid composer attachments for the current UX, and added progress events.
- MCP server and MCP Apps UI: added file ingest, explorer query/tag UI, source detail, search results, task visibility, and contract coverage.
- Migration discipline: added Alembic, initial migration, runtime migration mode, and schema drift tests.
- Explorer and performance passes: replaced the large hero/card layout with a dense explorer/chat shell, simplified the palette, memoized expensive React regions, bounded rendered lists, and added screenshot smoke checks.
- Documentation and operations: updated README and docs for architecture, ChatKit, MCP, auth, testing, migrations, storage, cleanup, and reindexing.
- Developer workflow: added VS Code launch support and ensured `npm run build:watch` exists.

## Data Model And Drift Guardrails

- ChatKit and app-core state should stay intentionally related:
  - `AppChatThread` stores thread metadata including selected-file scope.
  - `AppTask.origin_thread_id` links agent work back to ChatKit.
  - `AppChatAttachment` remains compatibility plumbing for older ChatKit attachment hosts.
  - `SourceFile`, `SemanticChunk`, tags, filesystem entries, generated assets, and task records are the app-owned source of truth.
- `SourceService.ingest_source` remains the canonical ingest path for web, ChatKit, MCP, and future Research Importer flows.
- Virtual paths are app-owned. OpenAI Files API IDs, vector-store chunk file IDs, and bucket object keys are implementation details tracked by source/chunk/filesystem records.
- Tags remain source-level metadata for the current baseline. OpenAI vector-store filtering uses canonical `tags` plus bounded exact-match tag slots.
- New schema changes must include Alembic migrations and drift tests.
- New app operations must update the capability matrix, Pydantic contracts, frontend types, ChatKit tools, MCP tools, and tests together.

## Local Environment And Verification

- `.env` is ignored and can be seeded from `../openai-vectorstore-mcp-app/.env` for shared local secrets.
- Keep `.env.example` light; optional settings should have defaults in `AppSettings`.
- Mandatory settings such as `OPENAI_API_KEY` should remain real settings and fail clearly if missing.
- Railway/S3-compatible storage is supported locally through `STORAGE_BACKEND=s3`, `S3_ENDPOINT`, `S3_BUCKET`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_REGION`, and `S3_URL_STYLE`; examples keep secret values empty.
- Playwright should run with Clerk disabled through test-only env overrides and local-dev auth enabled.

Current verification commands:

- `./.venv/bin/pyright`
- `./.venv/bin/pytest`
- `npm run typecheck`
- `npm run build`
- `npm run test:e2e`

## Open Questions

- Should Research Importer candidates appear in the main explorer tree before approval, or only inside a separate review queue until ingested?
- Should Research Importer provenance be stored directly on `SourceFile.metadata_json`, in separate import/candidate tables only, or both?
- Should ChatKit structured cards be built with ChatKit/MCP Apps widgets now, or should the next pass stay text/tool-first until importer behavior stabilizes?
- Should MCP eventually add a higher-level `ask_library_agent` tool, or should it stay primitive-tool-first unless a real host needs the wrapper?

## Official References Checked

- ChatKit custom backend: https://developers.openai.com/api/docs/guides/custom-chatkit
- ChatKit frontend embedding: https://developers.openai.com/api/docs/guides/chatkit
- MCP Apps resource templates and MIME type: https://developers.openai.com/apps-sdk/build/mcp-server#step-1--register-a-component-template
- MCP Apps data-tool/render-tool split: https://developers.openai.com/apps-sdk/build/chatgpt-ui#decoupled-pattern
- MCP Apps tool descriptor metadata: https://developers.openai.com/apps-sdk/reference#_meta-fields-on-tool-descriptor
- MCP Apps state management: https://developers.openai.com/apps-sdk/build/state-management#summary
