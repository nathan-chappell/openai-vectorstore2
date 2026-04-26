# Plan: AI File Browser, ChatKit, And MCP Apps

## Goal

Expose the app's core capabilities in product-ready surfaces:

1. Webapp: a Vite/React app where users browse an AI-powered virtual filesystem and drive the library through a ChatKit agent.
2. MCP: an MCP server exposing the same capability set, plus MCP Apps UI resources.
3. Shared app-core boundary: the prompt originally said "three forms" but listed two external forms. This plan treats the third form as the typed app-core capability layer that keeps REST, ChatKit, MCP, database models, and frontend contracts from drifting.

The product is now a file explorer first: ingestion stores files, publishes the original source file into an OpenAI vector store, and searches that source-level OpenAI index with app-owned filters for tags, path, source type, selected files, and date ranges. Semantic splitting remains available as an explicit tool/feature for inspection or derived chunk workflows, but it is no longer part of normal ingestion.

The capabilities to preserve and expose are OpenAI vector-store backed indexing/search, virtual folders/paths, tags, source inspection, path/date/type filtering, branch search, grounded QA, freeform generation, generated assets, task/history visibility, agent-mediated file selection, and optional semantic split preview/re-split tooling.

## Status Snapshot

- [x] Analyze backend service, model, persistence, OpenAI gateway, ChatKit, MCP, frontend, and UI test entry points.
- [x] Build the current baseline phases: migrations, task-based ingestion, PDF smart split, tag/vector-store reindexing, ChatKit tools, MCP tools/UI, Playwright, docs, and VS Code launch/build-watch support.
- [x] Rebuild the web product around a compact virtual filesystem explorer plus ChatKit.
- [x] Add a DB-backed virtual filesystem layer with folders, paths, rename/move/delete, selected-file scope, and OpenAI file-input preparation for ChatKit.
- [x] Promote and retain the new Research Importer feature plan as the next planned product feature.
- [x] Add the first Research Importer backend capability for fast library seeding and reference ingestion.
- [x] Apply the first browser/design fixes: hide empty preview, widen preview-on-selection, and add a persisted draggable explorer/chat splitter.
- [x] Add importer parser/normalizer coverage and refreshed Playwright shell assertions for hidden preview and splitter behavior.
- [x] Convert ingestion/search from semantic chunk publication to source-file vector-store indexing.
- [ ] Continue tightening explorer UX, ChatKit coordination, and Playwright coverage around real file-browser workflows.

## Current Direction Change: Direct Research Library Builds

Status: implementation pass completed.

Decision:

- The primary research workflow should not have an approval stage. A topic or paper seed should directly discover, dedupe, download, ingest, and show the resulting library.
- Candidate approval/rejection may remain as lower-level importer/debug primitives, but it should not be the main browser, ChatKit, or MCP Apps path.
- Duplicate handling is part of the builder contract: suppress duplicate URLs before persistence, suppress duplicate content hashes after download, and report skipped duplicate candidates clearly instead of creating duplicate files.
- ChatKit should be the preferred orchestration surface for research builds. It should stream status updates as discovery, reference expansion, download, duplicate skipping, and indexing progresses.
- The file browser and research panel should show progress affordances for downloading/indexing states so files entering the container feel alive rather than appearing only after completion.
- The ChatKit agent and pinned composer tool are the primary way to start research builds; the browser and MCP Apps panels should mainly mirror status and remain as fallback/manual surfaces.

## Current Direction Change: Source-File Vector Indexing

Status: completed for the current baseline.

Decision:

- Normal ingestion should not call semantic splitting and should not create semantic chunks as the default retrieval/indexing path.
- Normal ingestion should upload the original source file, attach that same source-level file to the OpenAI vector store, and store source-level vector attributes.
- Search should use OpenAI vector-store search over source files, then map results back to `SourceFile` records for display, QA, branch search, and ChatKit/MCP tools.
- App-owned filters should continue to guard behavior even if vector attributes are stale or missing: selected file IDs, source kind, tags, created date range, and virtual path/path prefix.
- Tag changes and file moves/renames should reindex the source-level vector-store file attributes instead of republishing semantic chunks.
- Semantic split preview/re-split remains available as a separate explicit feature. Re-split may create app-owned semantic chunk records for inspection or advanced workflows, but it should not be required for a newly ingested file to be searchable.

Implementation tasks:

- [x] Add source-level vector attributes, including `source_id`, `source_kind`, `virtual_path`, `virtual_name`, `created_at`, `tags`, bounded tag slots, and an indexing mode/version marker.
- [x] Add OpenAI gateway support for attaching an already-uploaded source file to a vector store with attributes.
- [x] Change ingest jobs to upload and attach the original/source-level file only, set the source ready when vector indexing completes, and keep `chunk_count=0` unless explicit split tooling is used.
- [x] Change search result mapping to source-level hits, synthesizing `ChunkHit`-compatible responses from OpenAI vector-store result text and source metadata until the public API is renamed.
- [x] Change reindex/tag/path updates to replace the source-level vector-store attachment with refreshed attributes.
- [x] Keep split preview and re-split tests/tooling, but update ingestion/search/reindex tests to assert no semantic split happens during normal ingest.
- [x] Update docs and wording away from “semantic RAG workspace” where it implies mandatory ingestion-time splitting.

## Checkpoint: 2026-04-26 Source-File Vector Indexing

Status: implementation pass completed.

Completed in this pass:

- Reworked normal ingestion so it no longer performs semantic splitting or publishes semantic chunk files. New ingests upload the source/original file, attach a source-level file to the OpenAI vector store with app-owned attributes, and return `chunk_count=0` unless split tooling is explicitly run.
- Added source-level vector index persistence through `SourceFile.openai_vector_file_id` and `SourceFile.vector_attributes_json`, plus an Alembic migration and schema/contract updates across REST, ChatKit, MCP, and frontend TypeScript.
- Updated vector attributes and filters around source-level metadata: `index_kind=source_file`, source ID/kind, virtual path/name, created date, canonical tags, bounded tag slots, selected source IDs, date ranges, and virtual path filters.
- Reworked search, branch search, grounded QA evidence, ChatKit tools, MCP tools, and MCP Apps copy to operate on indexed file matches rather than mandatory semantic chunks.
- Changed tag updates, move/rename path changes, and reindex jobs to replace the source-level vector-store file attachment with refreshed attributes.
- Kept semantic split preview/re-split as explicit tooling. Re-split can still create app-owned `SemanticChunk` rows for inspection, but those chunks are no longer required for source search and are not published as the default vector index.
- Updated README and operations/architecture/testing/migration docs to describe the file-explorer-first model with OpenAI vector-store backed source indexing.

Verification completed in this pass:

- `./.venv/bin/pyright`
- `./.venv/bin/pytest tests/integration/test_app_contracts.py tests/test_vector_attributes.py tests/test_migrations.py -q`
- `./.venv/bin/pytest -q`
- `./.venv/bin/ruff check backend tests`
- `npm run typecheck`
- `npm run build`
- `OPENAI_API_KEY=sk-test APP_SIGNING_SECRET=test-secret S3_ENDPOINT=http://127.0.0.1:9 S3_BUCKET=test-bucket S3_ACCESS_KEY_ID=test S3_SECRET_ACCESS_KEY=test npm run test:e2e -- --grep "workspace shell"`

Known remaining work:

- Run the full live `explorer-selected` Playwright flow with real OpenAI and S3-compatible values.
- Rename the public `search_chunks`/`ChunkHit` API shape once consumers are ready for a source-level naming change.
- Decide whether split-derived chunks should get any future advanced vector workflow, or remain inspection-only.

## Checkpoint: 2026-04-26 Follow-Up Pass

Status: implementation pass completed in the fresh clone.

Completed in this pass:

- Initialized the local repository with `.venv` on Python 3.14 and installed Node dependencies.
- Tightened Research Importer normalization: canonical URL dedupe now removes common tracking parameters/fragments/default ports, only accepts public HTTP(S) URLs, preserves public `href` targets during HTML cleanup, normalizes non-breaking spaces, cleans LinkedIn/exported HTML seeds, and dedupes review candidates against already-ingested source provenance.
- Added focused Research Importer tests for URL normalization, arXiv/PDF recognition, HTML cleanup, LinkedIn export cleanup, and provenance-based duplicate suppression.
- Refreshed Playwright shell assertions so empty browsing starts without `.explorer-detail`, mobile hides the splitter, desktop drag persists the workspace split, and selected-file coverage asserts preview visibility.
- Fixed the workspace grid splitter CSS so the stored split percentage controls the explorer/chat width directly instead of acting only as a grid-track maximum.

Verification completed in this pass:

- `./.venv/bin/pyright`
- `./.venv/bin/pytest -q`
- `./.venv/bin/ruff check backend/app/services/research.py tests/test_research_importer.py`
- `npm run typecheck`
- `npm run build`
- `OPENAI_API_KEY=sk-test APP_SIGNING_SECRET=test-secret S3_ENDPOINT=http://127.0.0.1:9 S3_BUCKET=test-bucket S3_ACCESS_KEY_ID=test S3_SECRET_ACCESS_KEY=test npm run test:e2e -- --grep "workspace shell"`

Known remaining work:

- Run the full live `explorer-selected` Playwright flow with real OpenAI and S3-compatible values.
- Continue refining the direct Research Library Builder panel after the browser/file-explorer direction settles.
- Continue adding normal file-browser flows for create folder, upload, selected-file grounded QA, reveal/go-to-location, rename/move, and delete.
- Consider whether `.gitignore` should use `tmp/` instead of `./tmp` and whether `tmp/image.png` should remain committed.

## Checkpoint: 2026-04-26 Wrap-Up

Status: implementation checkpoint committed for handoff.

Completed in the current checkpoint:

- Added the Research Importer backend foundation: database model/migration, source provenance metadata, Pydantic schemas, settings, OpenAI web-search discovery gateway, `ResearchImportService`, REST routes, ChatKit tools, MCP tools, capability-matrix entries, frontend API/types, and integration/contract coverage.
- Kept canonical ingestion through `SourceService.ingest_source`; imported seeds/candidates create normal source records, OpenAI files, vector-store files, tasks, and metadata rather than using a parallel ingest path.
- Added lower-level candidate state transitions for pending, approved, rejected, ingested, and failed candidates.
- Implemented the first explorer layout fixes: the preview pane is hidden until a file is selected, selected-file preview gets more room, folder navigation clears stale preview state, and the explorer/chat split is draggable and persisted in `localStorage`.
- Updated `plan.md` to preserve the Research Importer feature plan and browser/design queue.

Verification completed before pausing:

- `npm run typecheck`
- `./.venv/bin/pyright`
- `./.venv/bin/pytest tests/test_migrations.py tests/integration/test_app_contracts.py -q`

Known remaining work for the next session:

- Run the full live `explorer-selected` Playwright flow with real OpenAI and S3-compatible values.
- Continue refining the direct Research Library Builder panel after the browser/file-explorer direction settles.
- Consider whether `.gitignore` should use `tmp/` instead of `./tmp` and whether `tmp/image.png` should remain committed.

## Current Product Shape

The product is now centered on an AI-powered file explorer:

- The left surface behaves like a normal, compact file explorer with folders, files, query, tags, upload, rename, move, delete, preview, and selected-file scope.
- The preview pane should stay hidden until a file is selected. Once a file is selected, the preview should open wide enough to be useful for the selected media/text/PDF rather than remaining a cramped placeholder column.
- ChatKit sits beside the explorer and uses selected ready files as OpenAI file inputs. Composer attachments stay hidden in the current web UX.
- The horizontal split between the file explorer/preview side and ChatKit should be user-resizable with slider-like drag behavior, so users can allocate more width to browsing/previewing or to chat as needed.
- OpenAI vector stores remain the source-level retrieval and tag-filtered search layer.
- The app database owns the virtual filesystem, task lifecycle, tags, provenance, ChatKit tables, source/chunk records, generated assets, and all relationships needed to prevent data-model drift.
- MCP exposes the same app-core capabilities as tools and Apps UI resources.

## Current Architecture Alignment

Backend:

- FastAPI wires services, REST, ChatKit, MCP, static frontend serving, and auth.
- `SourceService` owns ingest, PDF/text extraction, source-level OpenAI file/vector-store publication, optional semantic splitting, tags, virtual filesystem operations, selected-file preparation, reindexing, and cleanup.
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

## Next Planned Feature: Research Library Builder

Status: direct-build workflow pivot completed for the current baseline.

Intent:

The user should be able to give the agent a topic or paper title, such as "Attention Is All You Need", and have the app build a bounded research library under an obvious workspace folder. The builder should find a primary paper when applicable, discover cited and related references, fetch public materials, ingest selected items through the normal source pipeline, and attach useful metadata such as description, summary, source date, path, tags, file type, provenance, and confidence. A default cap around 50 sources keeps the behavior useful without letting discovery run away.

Current foundation already completed:

- [x] Persistent `ResearchImportService`, candidate records, source provenance metadata, REST routes, ChatKit tools, MCP tools, frontend API/types, capability matrix entries, and migration/contract coverage.
- [x] Seed support for pasted text, public URL, PDF/arXiv URL, uploaded file, and exported LinkedIn HTML/text.
- [x] Fetching, URL normalization, content hashing, duplicate suppression, lower-level candidate transitions, and canonical ingestion through `SourceService.ingest_source`.
- [x] OpenAI web-search discovery for first-pass candidate collection, bounded by settings.
- [x] Parser/normalizer tests and backend integration tests for seed-to-candidate, direct-build ingestion, and lower-level approved-candidate ingestion flows.

Refined product decisions:

- Build the library builder on top of the importer instead of creating a parallel agent path.
- Treat a topic or paper title as a first-class seed. It should not be ingested as a tiny text file unless the user explicitly asks; it should primarily drive discovery.
- Create or reuse a workspace folder for each builder run, using a clean title such as `/Research/Attention Is All You Need` when no folder is supplied.
- Store richer metadata on both candidates and ingested source files through `provenance_json` and `SourceFile.metadata_json`: description, summary, suggested/model tags, authors, published date when known, DOI/arXiv ID when known, discovery query, discovery depth, parent source/candidate, normalized URL, fetched URL, content hash, and fetch timestamp.
- Use actual source tags for high-confidence model tags when ingesting builder-created sources, while retaining raw suggested tags in metadata.
- Keep lower-level candidate review tools available for debugging/manual importer work, but make the normal builder a direct discovery-to-ingestion workflow with skipped duplicates.
- Use ChatKit progress updates for build stages and per-reference download/index state; the browser panel should render candidate/file progress rather than approval controls.
- Expose research building as a pinned ChatKit composer tool and starter prompt so users naturally begin the workflow through the agent.
- Preserve the non-bypass policy: login walls, paywalls, and anti-bot gates become failed/degraded candidates, never scraping targets to work around.

Implementation track:

- [x] Add topic/paper seed contracts and discovery prompting so a bare title can find primary papers plus useful references.
- [x] Add candidate/source metadata fields in API responses: `description`, `summary`, `suggested_tags`, and optional publication/authorship fields without requiring a table migration yet.
- [x] Create or reuse a research folder automatically for topic/paper runs, and route seed/candidate ingestion into that folder.
- [x] Add an agent-facing `build_research_library` operation that creates the folder, discovers candidates, auto-ingests public items up to `max_sources`, skips duplicates, and records progress/results on the task.
- [x] Replace the compact web Research Import/Library Builder review controls with direct build status, candidate progress, and duplicate/failed state display.
- [x] Extend discovery beyond one hop by deriving follow-up queries from candidate metadata, bounded by `max_depth`, `max_candidates_per_source`, and `max_pending_candidates`.
- [x] Update MCP Apps UI resources so the main research builder path builds directly and shows statuses instead of approve/reject controls.
- [x] Add ChatKit client/widget coordination so the agent can open the research builder panel, stream progress, and show candidate/task state while it works.
- [x] Add a research action over the built files: ask a question, retrieve evidence, and return cited results with source references.

Verification plan:

- [x] Integration test: topic seed with fake discovery creates a research folder, pending candidates, and enriched metadata without ingesting the raw topic as a source.
- [x] Integration test: build mode creates the foldered research library task and bounded candidates, with deterministic public-candidate ingest coverage.
- [x] UI shell assertion: the browser exposes the research builder input and disabled build action in the explorer surface.
- [x] Integration test: build mode expands follow-up candidates to depth 2 and records `parent_candidate_id` links.
- [x] Integration test: build mode auto-ingests fake public candidates into the research folder through `SourceService.ingest_source`.
- [x] Integration test: build mode skips duplicate downloaded content by content hash and reports duplicate candidates without creating duplicate files.
- [x] Contract tests: backend schemas, frontend TypeScript, REST, ChatKit tools, MCP tools, and capability matrix stay aligned.
- [x] UI/Playwright test: user starts a research library build from the browser, sees direct ingestion progress/status, and sees ingested files in the created folder.
- [x] Re-run `./.venv/bin/pyright`, focused backend tests, `npm run typecheck`, `npm run build`, and targeted Playwright checks for each checkpoint.

## Checkpoint: 2026-04-26 Direct Research Builder Pivot

Status: implementation pass completed.

Completed in this pass:

- Removed approval/reject controls from the primary browser and MCP Apps research builder flow.
- Changed build mode to ingest pending candidates directly while keeping lower-level approval primitives available for manual importer/debug use.
- Added fetch-time duplicate detection by normalized URL and downloaded content hash, marking skipped candidates as `duplicate` and avoiding duplicate source files.
- Added ChatKit progress callbacks for discovery, depth expansion, downloads, duplicate skips, and indexing queue updates.
- Added a pinned ChatKit composer tool and starter prompt for research builds, making ChatKit the primary start point.
- Added candidate progress bars in the research panel and file-status progress bars in the file browser for processing/ready/failed states.
- Updated capability descriptions, ChatKit instructions, MCP Apps copy, TypeScript contracts, and Playwright coverage for the direct-build workflow.
- Added compact file-browser keyboard shortcuts for rename, navigate-up, and delete, and trimmed those buttons from the explorer command bar.
- Added a Playwright escape hatch, `PLAYWRIGHT_REUSE_EXISTING_SERVER=true`, so local tests can reuse an already-running dev backend instead of colliding with it.

Verification completed in this pass:

- `./.venv/bin/ruff check backend/app/services/research.py backend/app/chatkit/server.py backend/app/mcp/server.py backend/app/core/capabilities.py tests/integration/test_app_contracts.py`
- `./.venv/bin/pyright`
- `./.venv/bin/pytest tests/integration/test_app_contracts.py -q`
- `npm run typecheck`
- `npm run build`
- `OPENAI_API_KEY=sk-test APP_SIGNING_SECRET=test-secret S3_ENDPOINT=http://127.0.0.1:9 S3_BUCKET=test-bucket S3_ACCESS_KEY_ID=test S3_SECRET_ACCESS_KEY=test npm run test:e2e -- --grep "research library builder directly"`
- `OPENAI_API_KEY=sk-test APP_SIGNING_SECRET=test-secret S3_ENDPOINT=http://127.0.0.1:9 S3_BUCKET=test-bucket S3_ACCESS_KEY_ID=test S3_SECRET_ACCESS_KEY=test npm run test:e2e -- --grep "workspace shell"`
- `PLAYWRIGHT_REUSE_EXISTING_SERVER=true OPENAI_API_KEY=sk-test APP_SIGNING_SECRET=test-secret S3_ENDPOINT=http://127.0.0.1:9 S3_BUCKET=test-bucket S3_ACCESS_KEY_ID=test S3_SECRET_ACCESS_KEY=test npm run test:e2e -- --grep "file explorer shortcuts|workspace shell"`

## Browser And Design Fix Queue

Status: active intake; implement items as concrete plans arrive.

- [x] Hide the preview surface until a file is selected so empty browsing starts as a clean explorer plus ChatKit layout.
- [x] When a file is selected, open a preview area wide enough for the selected media/text/PDF instead of leaving a narrow placeholder.
- [x] Add a draggable horizontal splitter between the explorer/preview region and ChatKit, with sensible min/max widths and persisted preference.
- [ ] Keep the file explorer dense, fast, and familiar: normal folder/file rows, tight metadata, simple affordances, no marketing-style cards or explanatory copy.
- [x] Validate hidden-preview and splitter fixes with Playwright desktop and mobile screenshots before marking complete.
- [x] Add compact file-browser keyboard shortcuts: `F2` renames the focused item, `Backspace` navigates up one folder, and `Delete` deletes the current selection.
- [ ] Add future browser/design plans here first, then move them into implementation tasks as soon as enough detail exists.

## Near-Term Follow-Ups

- Implement Research Library Builder as the next substantial feature track, starting with topic/paper seeds, richer metadata, automatic folder placement, and agent-facing build mode.
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
- Virtual paths are app-owned. OpenAI Files API IDs, source-level vector file IDs, legacy vector-store chunk file IDs, and bucket object keys are implementation details tracked by source/chunk/filesystem records.
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
- OpenAI vector-store files with attributes: https://developers.openai.com/api/docs/assistants/tools/file-search#creating-vector-stores-and-adding-files
