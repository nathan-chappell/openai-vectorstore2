# Plan: AI File Browser, ChatKit, And MCP Apps

## Purpose

Build a product-ready workspace for managing, searching, and using a personal research library.

The app has three coordinated surfaces:

1. Web app: a Vite/React file-library workspace with ChatKit beside it.
2. MCP: tools and MCP Apps resources exposing the same library capabilities.
3. App core: typed services, schemas, database models, and tests that keep REST, ChatKit, MCP, and frontend contracts aligned.

The product is file-library first. Ingestion stores the original source file, publishes that source file to an OpenAI vector store, and searches the source-level index with app-owned filters for tags, paths, source type, selected files, and dates. Semantic splitting remains available as an explicit inspection or re-split tool, but it is not part of normal ingestion.

## Current Priorities

### 1. Split Explorer And Library Views

Status: planned next browser cleanup.

Decision:

- The file explorer and tag/query filtering should become separate first-class views.
- Explorer view should feel like a normal file browser: folders, files, preview, rename, move, delete, upload, selection, keyboard shortcuts, and lightweight metadata.
- Library view should focus on filtering and discovery: query, tags, type/date filters, result summaries, and source metadata. Tag controls should live primarily here instead of crowding the explorer.
- ChatKit should work with both views: selected files from Explorer and filtered/search results from Library should be usable as agent context.

Implementation notes:

- Add a clear view switcher or tabs for Explorer and Library.
- Keep the existing virtual filesystem as the source of truth for folders and paths.
- Reuse the same source/search APIs; change the browser layout and interaction model first.
- Preserve fast keyboard behavior in Explorer: `F2` rename, `Backspace` or `Alt+Left` go up, `Delete` delete, and Shift+arrow range selection.
- Keep preview closable and resizable so file details stay readable.

Acceptance criteria:

- Explorer no longer needs prominent tag chips or dense search controls to feel complete.
- Library filtering has enough space to show tags, query, result status, and source metadata clearly.
- Selecting or revealing a file from Library opens the correct file in Explorer.
- Playwright covers switching views, tag filtering, file reveal, selection, and preview behavior.

### 2. Evidence Annotations

Status: planned.

Goal:

- Grounded and research answers should include inline evidence annotations.
- Clicking an annotation should reveal/select the matching source in the explorer and open the best available preview location.
- The implementation should preserve source/result provenance so the UI can make trust visible without relying on prose citations alone.

Implementation notes:

- Prefer native ChatKit annotations if custom source annotations are supported.
- If native inline annotations are not available, build a compact evidence widget with source chips that call `reveal_file`.
- Keep source IDs, file IDs, locators, and task IDs in structured tool results.

### 3. ChatKit Stability Verification

Status: partially fixed; needs live verification.

Completed:

- Stabilized ChatKit client-tool state handling to avoid React nested update loops.
- Deferred app state mutations from `handleClientTool`.
- De-duped repeated research-builder ingested updates.

Next:

- Re-run a streamed research-library ChatKit flow in the browser.
- Capture browser console output and backend logs.
- Confirm the minified React #185 / maximum update depth error is gone.
- Fix any remaining refresh, polling, or event-callback loop.

### 4. Browser Workflow Coverage

Status: ongoing.

Add Playwright coverage for normal file-library work:

- Create folder.
- Upload text/json/PDF.
- Navigate folders.
- Rename and move.
- Select files and ask ChatKit a grounded question.
- Reveal a source from ChatKit or Library view.
- Delete files and folders with progress/status feedback.

## Completed Baseline

### Source-File Vector Indexing

Status: complete.

- Normal ingestion uploads the source/original file and attaches it to the OpenAI vector store.
- Search maps source-level vector results back to `SourceFile` records.
- App-owned filters guard behavior even when vector attributes are stale or missing.
- Tag changes and path changes reindex source-level vector attributes.
- Split preview and re-split remain explicit tools, not default ingestion steps.

### Virtual Filesystem

Status: complete.

- Added DB-backed folders, paths, source/file entries, recursive delete, rename, move, and current-folder ingest.
- Added source path attributes to vector metadata within OpenAI metadata limits.
- Added selected-file preparation for ChatKit file inputs.
- Exposed filesystem operations through REST, ChatKit tools, MCP tools, and the frontend.

### Research Library Builder

Status: complete for the current baseline.

- A topic or paper title can create/reuse a research folder and build a bounded library.
- Discovery finds primary and related public references.
- Public candidates are fetched, deduped by normalized URL and content hash, and ingested through `SourceService.ingest_source`.
- The primary workflow has no approval step; lower-level candidate review remains available for debugging/manual use.
- Metadata is stored on candidates and source files: description, summary, suggested tags, authors, published date, DOI/arXiv ID, discovery depth, parent reference, normalized URL, fetched URL, content hash, and fetch timestamp.
- Research answers stay scoped to task-linked files and wait for indexing instead of falling back to whole-library search.
- Search accepts both source IDs and explorer entry IDs where source scope is expected.

Follow-ups:

- Make progress text useful but not overly technical.
- Keep download/index progress visible in ChatKit and the browser.
- Add inline evidence annotations for research answers.

### ChatKit And MCP

Status: complete for the current baseline.

- ChatKit tools cover source listing, filesystem operations, tags, search, branch search, research build, research answers, split preview/re-split, generated assets, and task visibility.
- MCP exposes the same core capabilities as tools and Apps resources.
- ChatKit threads persist selected-file scope and OpenAI conversation state.
- ChatKit agent runs use OpenAI conversation IDs for durable context and track response IDs for logs/debugging.
- Client-tool calls coordinate file selection, file reveal, search changes, and research builder state.
- ChatKit treats selected files as retrieval scope first; direct file inputs are capped to a small number of small files and are only attached on user-message turns.
- ChatKit Responses requests enable server-side context compaction with a configurable compact threshold.

### Logging And Debugging

Status: complete for the current baseline.

- Backend OpenAI calls log response IDs, conversation IDs, request IDs, model/status, token totals, duration, and clickable platform log URLs.
- FastAPI request logs and framework logs reach the configured file log.
- `skills/openai-log-debugger` can fetch logged Responses and Conversations artifacts into `.local/openai-debug/`.
- Logs avoid prompts, secrets, and bulky response bodies.

### Browser UX Fixes

Status: mostly complete; view split remains next.

- Hidden empty preview.
- Wider selected-file preview.
- Persisted explorer/chat splitter.
- In-app delete confirmation with delete progress.
- Recursive-folder delete warning.
- Arrow-key focus and Shift+arrow range selection.
- `F2`, `Backspace`, `Alt+Left`, `Delete`, and `?` shortcut help.
- Closable/resizable preview.
- Reduced active-task polling to a slower background cadence with targeted refreshes after actions.

## Architecture Guardrails

- `SourceService.ingest_source` remains the canonical ingest path for web, ChatKit, MCP, and research builds.
- `SourceFile`, filesystem entries, tags, tasks, generated assets, and ChatKit records are the app-owned source of truth.
- OpenAI file IDs, vector-store file IDs, bucket keys, and response/conversation IDs are implementation details tracked in app records or logs.
- Virtual paths are app-owned and should stay stable across REST, ChatKit, MCP, and frontend behavior.
- Prefer passing app IDs, source IDs, task IDs, and resource URLs between surfaces; fetch full resource content through APIs, tools, or cache lookups instead of copying bulky content through chat state.
- Tags remain source-level metadata. OpenAI vector filtering uses canonical `tags` plus bounded exact-match tag slots.
- New schema changes require Alembic migrations and drift tests.
- New app operations should update the capability matrix, Pydantic contracts, frontend types, ChatKit tools, MCP tools, and tests together.
- Prefer integration tests for behavior. Add unit tests only for tricky parsing, normalization, or selection logic.

## Verification

Current standard checks:

- `./.venv/bin/ruff check backend tests`
- `./.venv/bin/pyright`
- `./.venv/bin/pytest -q`
- `npm run typecheck`
- `npm run build`
- `npm run test:e2e`

Targeted checks to keep using:

- `npm run test:e2e -- --grep "workspace shell"`
- `npm run test:e2e -- --grep "file explorer shortcuts"`
- `npm run test:e2e -- --grep "research library builder directly indexes"`

## Local Environment

- Python is `python3.14` in `.venv`.
- `.env` is ignored and can be seeded from a neighboring local project when needed.
- Keep `.env.example` light; optional settings should have defaults in `AppSettings`.
- Mandatory settings such as `OPENAI_API_KEY` should remain required and fail clearly if missing.
- Local/S3-compatible storage is supported through `STORAGE_BACKEND`, `S3_ENDPOINT`, `S3_BUCKET`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_REGION`, and `S3_URL_STYLE`.
- Playwright should run with Clerk disabled through test env overrides and local-dev auth enabled.

## Open Questions

- Should split-derived chunks stay inspection-only, or should they get an advanced vector workflow later?
- Should the public API rename `search_chunks` / `ChunkHit` now that normal retrieval is source-file based?
- Should ChatKit source annotations use native inline annotations, a custom evidence widget, or both?
- Should MCP eventually add a higher-level `ask_library_agent` tool, or stay primitive-tool-first until a host needs an agent wrapper?

## Completed History

Major completed phases:

- App-core capability matrix and cross-surface contract tests.
- Task-based ingestion with `AppTask` lifecycle tracking.
- PDF smart split, split preview, and re-split tooling.
- Tag model, tag filters, vector attribute versions, and reindexing on tag/path changes.
- ChatKit web workspace and tools.
- MCP server and MCP Apps resources.
- Alembic migrations and schema drift tests.
- Dense explorer/chat shell replacing the older broad workbench layout.
- README and operations docs for architecture, ChatKit, MCP, auth, testing, migrations, storage, cleanup, and reindexing.
- VS Code launch support and `npm run build:watch`.

## Official References Checked

- ChatKit custom backend: https://developers.openai.com/api/docs/guides/custom-chatkit
- ChatKit frontend embedding: https://developers.openai.com/api/docs/guides/chatkit
- ChatKit entity callbacks: https://developers.openai.com/api/docs/guides/chatkit-themes#enable-mentions-in-the-composer-with-entity-tags
- Responses web-search output annotations: https://developers.openai.com/api/docs/guides/tools-web-search#output-and-citations
- Citation formatting guidance: https://developers.openai.com/api/docs/guides/citation-formatting
- MCP Apps resource templates and MIME type: https://developers.openai.com/apps-sdk/build/mcp-server#step-1--register-a-component-template
- MCP Apps data-tool/render-tool split: https://developers.openai.com/apps-sdk/build/chatgpt-ui#decoupled-pattern
- MCP Apps tool descriptor metadata: https://developers.openai.com/apps-sdk/reference#_meta-fields-on-tool-descriptor
- MCP Apps state management: https://developers.openai.com/apps-sdk/build/state-management#summary
- OpenAI vector-store files with attributes: https://developers.openai.com/api/docs/assistants/tools/file-search#creating-vector-stores-and-adding-files
