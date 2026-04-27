# Plan: AI File Browser, ChatKit, And MCP Apps

## Purpose

Build a product-ready workspace for managing, searching, and using a personal research library.

The app has three coordinated surfaces:

1. Web app: a Vite/React file-library workspace with ChatKit beside it.
2. MCP: tools and MCP Apps resources exposing the same library capabilities.
3. App core: typed services, schemas, database models, and tests that keep REST, ChatKit, MCP, and frontend contracts aligned.

The product is file-library first. Ingestion stores the original source file, publishes that source file to an OpenAI vector store, and searches the source-level index with app-owned filters for tags, paths, source type, selected files, and dates. Semantic splitting remains available as an explicit inspection or re-split tool, but it is not part of normal ingestion.

## Wrap-Up Checklist For 0.1.0 Beta

Goal:

- Get the project to a polished, realistic demo state: a credible RAG system, a repeatable agentic workflow, advanced OpenAI API usage, and a live project that is strong enough to reference from a resume.
- Deploy it as a sister service to PlodAI with roughly the same production posture. Assume `../plodai` is good enough as the deployment reference and this project mainly needs its own database/storage/API environment values.
- Target beta-production readiness, not a large new roadmap. Finish the important proof points, document them, and avoid taking on nonessential product expansion before the first deployed version.

Release scope:

- Version starts at `0.1.0` for both Python and frontend package metadata.
- Primary demo path: upload/build a small research library, inspect files, run tag and semantic search, ask grounded questions with evidence, generate a structured report, render/preview/export it, and show logs/progress/cost tracking for OpenAI-backed work.
- The project should demonstrate source-level OpenAI vector-store RAG, ChatKit agent tools, MCP surface area, task/progress updates, generated artifacts, billing/usage accounting foundations, and deployable app architecture.

Functional final checks:

- Verify tag filtering and semantic Library search with a realistic library: all/any tag mode, nonblank fallback query, source metadata display, selected-result chat scope, file reveal, and evidence links.
- Verify grounded ChatKit answers from selected Explorer files and Library search results, including citation clicks and browser-side reveal behavior.
- Verify research-builder flow on a small topic: discovery, ingest, vector indexing, scoped answer, progress visibility, and log traceability.
- Verify report generation end to end: structured draft, Markdown render with KaTeX-compatible math/evidence links, saved library artifact, PDF render path when implemented, PDF inspection/retry behavior, and download links that point at saved artifacts.
- Verify generated assets and stored artifacts are reachable from the library, selectable for ChatKit context where appropriate, and covered by cleanup/delete flows.
- Verify billing/usage foundations are acceptable for beta: activation gate, admin credit grant, low-credit block, cost event creation for the main expensive paths, and clear logs with response/conversation IDs.
- Run the standard verification suite plus at least one browser smoke pass against a seeded realistic library.

Deployment checklist:

- Add a Dockerfile that builds the Vite frontend, installs the Python app in the `.venv`/package style expected by the repo, runs Alembic migrations or documents the migration command, and starts the backend HTTP service.
- Add a `.dockerignore` that excludes `.venv`, `node_modules`, local logs, local storage, debug artifacts, Playwright output, and secrets.
- Add a Railway deployment guide or config notes covering service start command, health check, required env vars, storage choice, and migration workflow.
- Prefer Docker deploys for Railway. Publish images as `nathanschappell/openai-vectorstore2:0.1.0` and later tags with `docker push nathanschappell/openai-vectorstore2:tagname`.
- Provision a Railway Postgres database if it can sleep or otherwise fits the beta budget. Match PlodAI's deployment pattern where possible and switch this app's DB env vars to the new service.
- Confirm mandatory env vars are documented: `OPENAI_API_KEY`, database URL, Clerk values if auth is enabled, storage backend/S3-compatible values if not using local ephemeral storage, app base URL, allowed origins, billing/admin settings, and any ChatKit/OpenAI model settings.
- Decide beta storage explicitly: local container storage is acceptable only for throwaway demos; persistent Railway volume or S3-compatible storage is preferred for a live resume link.
- Confirm logs work in Railway without leaking prompts, secrets, or bulky content, and that enough IDs are present to debug OpenAI API calls from platform logs.

Resume/demo checklist:

- Update README with a concise feature list, architecture diagram or section, local run commands, Docker/Railway deployment notes, and a demo script.
- Add screenshots or a short walkthrough showing Explorer, Library semantic search, ChatKit grounded answer, report artifact, and deployment/runtime logs if useful.
- Call out the strongest technical points plainly: OpenAI vector-store file search with app-owned metadata filters, ChatKit agent tools, MCP adapters, task progress, typed Python/TypeScript contracts, usage accounting, and artifact/report workflows.
- Keep the live beta route stable enough to show, but make clear in copy and logs that it is beta and may use admin-gated access.

## Current Priorities

### 0. Typed Payload And Legacy Compression Refactor

Status: first pass implemented; continue opportunistic cleanup.

Goal:

- Reduce code duplication, type drift, and bare-dict handling across app-core services.
- Prefer `TypedDict` for compatibility payloads, Pydantic for validated API contracts, dataclasses for arbitrary runtime structures, and normal classes only where behavior is significant.
- Keep SQLAlchemy JSON columns as database-native dictionaries, but access common shapes through typed properties so service code does not repeatedly cast or manually normalize the same payloads.
- Remove compatibility, legacy, and migration-only code paths that are not needed before a production instance exists.

Implementation plan:

- Completed first pass: inventoried the highest-traffic JSON payloads; added shared typed-dict aliases for source metadata, research provenance, OpenAI usage, vector attributes, structured objects, and task payloads; added SQLAlchemy model accessors for `SourceFile.source_metadata`, `SourceFile.vector_attributes`, `ResearchImportCandidate.provenance`, `StoredAsset.asset_metadata`, `CostEvent.raw_usage`, task object payloads, and ChatKit metadata/status payloads; and refactored source, research, billing, and ChatKit store hotspots to use them.
- Be careful with method covariance and invariant containers: build with narrow local types, then assign or cast at the final wider return/override boundary when required.
- Remove helpers/normalizers made redundant by stronger typed boundaries, and drop legacy chunk/vector compatibility code where tests show the current source-level indexing flow no longer depends on it.
- Keep edits incremental with integration-level coverage; add unit tests only for tricky parsing or normalization logic that remains.

Acceptance criteria:

- Pyright stays clean without `type: ignore`.
- Common JSON shapes are named and reused instead of re-declared as `dict[str, object]` throughout services.
- SQLAlchemy JSON payloads are read/written through typed properties where practical.
- Removed legacy code has either no remaining call sites or a current test that proves the replacement path.

### 1. TypeScript Contract And Legacy Cleanup

Status: first utility/contract extraction implemented; continue component/API cleanup.

Goal:

- Reduce frontend type drift, duplicate payload shapes, and compatibility code across React components, API clients, ChatKit client tools, and tests.
- Use TypeScript deliberately to model the app's actual contracts without importing Python-specific patterns such as Pydantic or dataclasses.
- Make API, task, filesystem, ChatKit, billing, report, and generated-asset payloads easier to evolve without scattered `any`, `unknown`, ad hoc casts, or repeated object literals.
- Remove legacy and compatibility branches that are no longer needed before production data exists.

Implementation plan:

- Completed first pass: extracted shared app contracts, workspace constants, ChatKit client-tool payload shape, filesystem fuzzy search, research-result merge/narrowing helpers, UI formatting helpers, and local UI-state helpers from the current large `App.tsx`.
- Completed legacy compression pass: removed the dead legacy source-list app path and its duplicate source fetch/filter/upload explorer code.
- Completed component split pass: moved source preview, raw content preview, and chunk-row rendering into a focused typed React component module.
- Completed ChatKit component split: moved ChatKit rendering/configuration into a focused typed component while keeping app-owned callbacks in `App.tsx`.
- Completed header component split: moved workspace header presentation into a focused typed component.
- Completed Library view component split: moved tag/semantic search controls and result-row rendering into a focused typed component.
- Completed Explorer widget split: moved file rows and explorer/delete shortcut dialogs into focused typed components.
- Completed research-builder component split: moved research candidate controls/status rendering into a focused typed component.
- Completed Explorer shell split: moved the remaining Explorer/Library pane shell and keyboard handling into a focused typed component, leaving `App.tsx` primarily as state orchestration.
- Inventory repeated TypeScript shapes for sources, explorer entries, library results, tags, task status/progress, ChatKit tool payloads, billing summaries, generated assets, and future report artifacts.
- Next pass should split the remaining explorer/library workspace components into focused modules and continue moving API boundary payloads out of component code.
- Create or consolidate shared frontend contract modules under the existing type/API organization, using `type` aliases, `interface`s, discriminated unions, branded/string ID aliases where useful, and mapped/utility types when they remove real duplication.
- Keep runtime parsing at API boundaries explicit and lightweight. Use structured guards only where external or optional data genuinely needs narrowing; do not add a heavy validation library unless the codebase has a clear need.
- Prefer discriminated unions for task states, artifact kinds, source kinds, ChatKit client-tool events, and render/export statuses so switch statements become exhaustive and UI state cannot silently drift.
- Type API client functions from their request/response contracts and let React state derive from those contracts instead of redefining local component copies.
- Replace repeated string literals for route names, task kinds, source kinds, artifact kinds, and status values with shared literal unions or constants where that improves safety without making the code harder to read.
- Remove old compatibility handling, duplicate normalization, stale optional fields, and broad `Record<string, unknown>` plumbing once call sites are migrated.
- Keep edits incremental and verify with `npm run typecheck`, focused component tests if present, and Playwright coverage for affected user flows.

Acceptance criteria:

- Common frontend payloads are named once and reused across API clients, components, ChatKit tool handlers, and tests.
- `npm run typecheck` remains clean without new `any` escapes or broad casts.
- Important UI state machines use discriminated unions or equivalent exhaustive typing for impossible-state reduction.
- Legacy/compat TypeScript code has either no remaining call sites or a current test proving the replacement behavior.
- Frontend types stay aligned with backend schemas and generated/handwritten API contracts; mismatches are caught at compile time where practical.

Non-goals for the first pass:

- Do not introduce generated TypeScript contracts, runtime validation libraries, or a new state-management framework until the hand-written contracts and module boundaries are cleaned up.
- Do not restyle the UI while extracting types and helpers except where necessary to preserve behavior.

### 2. Split Explorer And Library Views

Status: first pass implemented; needs browser polish and follow-up coverage.

Decision:

- The file explorer and tag/query filtering should become separate first-class views.
- Explorer view should feel like a normal file browser: folders, files, preview, rename, move, delete, upload, selection, keyboard shortcuts, and lightweight metadata.
- Library view should focus on filtering and discovery: query, tags, type/date filters, result summaries, and source metadata. Tag controls should live primarily here instead of crowding the explorer.
- ChatKit should work with both views: selected files from Explorer and filtered/search results from Library should be usable as agent context.

Implementation notes:

- Completed first pass: added Explorer/Library tabs, local current-folder fuzzy filtering for Explorer, a semantic Library query surface with tag pills and `all` / `any` mode, Enter replace search, Ctrl+Enter append/dedupe search, ChatKit `set_file_search` routing to Library, and fuzzy ChatKit composer entity search over known file entries.
- Keep the existing virtual filesystem as the source of truth for folders and paths.
- Explorer search is local to the current folder and should be simple/fuzzy over entry name, path, description, summary, suggested tags, and source metadata that is already present in the folder listing. It must not call OpenAI vector search.
- Reuse the same fuzzy matching behavior for ChatKit composer entity search so file tags feel consistent with the Explorer.
- Library view is tag/semantic-search focused: start from a nonblank fallback query when the field is empty, show tags as pill-style multi-select controls, support `all` / `any` tag matching, and run OpenAI-backed source search from the query bar.
- In Library view, `Enter` replaces the current result set and `Ctrl+Enter` appends/dedupes into it.
- Results from Library view should be selectable as ChatKit file scope and reveal/open the corresponding file in Explorer.
- ChatKit `set_file_search` should move the user to Library view and apply query/tag filters instead of crowding Explorer.
- Preserve fast keyboard behavior in Explorer: `F2` rename, `Backspace` or `Alt+Left` go up, `Delete` delete, and Shift+arrow range selection.
- Keep preview closable and resizable so file details stay readable.

Next:

- Completed follow-up: Library rows fall back to vector-result attributes such as `virtual_name` and `virtual_path` when a semantic hit is not already in the local entry cache.
- Completed follow-up: Library rows now have direct chat-scope checkboxes plus a bulk “Select results” action.
- Consider moving research builder into Library view or a third task-focused area so Explorer stays purely file-browser oriented.

Acceptance criteria:

- Explorer no longer needs prominent tag chips or dense search controls to feel complete.
- Explorer search filters only the current folder and remains responsive without billing or OpenAI calls.
- Library filtering has enough space to show tags, query, result status, and source metadata clearly.
- Empty Library query submissions use a deliberate fallback query rather than sending a blank API request.
- `Enter` and `Ctrl+Enter` semantics are covered in the browser UI and do not lose existing selected chat scope.
- Selecting or revealing a file from Library opens the correct file in Explorer.
- Explorer hotkeys work when focus is inside the file list rows, including after mouse selection.
- Playwright covers switching views, tag filtering, file reveal, selection, and preview behavior.

### 3. Evidence Annotations

Status: first pass implemented; needs browser verification.

Goal:

- Grounded and research answers should include inline evidence annotations.
- Clicking an annotation should reveal/select the matching source in the explorer and open the best available preview location.
- The implementation should preserve source/result provenance so the UI can make trust visible without relying on prose citations alone.

Implementation notes:

- ChatKit-facing retrieval and answer tools now return compact source records with source ID, name, type, path, description, summary, tag slugs, locator, and `citation_link`.
- The agent is instructed to cite evidence with markdown links that use `chatkit-link://source?...`.
- The frontend handles ChatKit deeplink events and composer entity clicks by revealing the matching file in Explorer.
- Composer entity search can find files/folders and insert clickable file tags.
- Native output annotation support still needs live verification; the current baseline uses ChatKit deeplinks and entity callbacks.

Next:

- Verify a grounded ChatKit answer in the browser and confirm citation clicks reveal the source.
- If ChatKit exposes richer output annotations for custom source entities, replace or augment markdown links with native annotations.
- Consider an evidence widget for answer summaries that need a stable source list outside prose.

### 4. Report Compilation And Export

Status: first foundation pass implemented.

Goal:

- Agents should be able to create, revise, persist, preview, and export report documents from library context.
- A report should be stored as a loosely structured document that maps naturally to Markdown and supports math notation such as KaTeX.
- Reports should be first-class library artifacts: creation writes the structured report into the library, and generated previews/exports are saved back into the library before download.

Implementation notes:

- Completed first pass: added a Pydantic structured report document model for title, sections, prose blocks, lists, tables, citations/evidence links, equations/math blocks, and figures/assets.
- Completed first pass: added a canonical Markdown renderer that preserves citation links, KaTeX-compatible display/inline math, escaped tables, lists, figures, and references.
- Next pass: add persistence and API/task boundaries so structured reports and compiled Markdown become library artifacts.
- Later pass: optionally render PDF from the same structured report source.
- Store compiled report artifacts through the same library/storage boundaries used for source files and generated assets, so reports can be searched, selected for ChatKit scope, previewed, downloaded, and cited later.
- ChatKit should expose agent tools to draft/update a structured report, compile it, save it into a folder, render Markdown preview, render PDF preview, and return library links to the saved artifacts.
- PDF download should normally mean "render PDF, save it in the library, then offer the saved library artifact for download" rather than producing an unmanaged transient file.
- Add a PDF inspection loop for the PDF-producing agent: render PDF pages to images with `pdf-lib`, `pdf.js`, or an equivalent renderer; send page images to a 5.4-class vision model for layout/content critique; if the critique is good enough, publish the PDF, otherwise revise and re-render once or twice before returning the best PDF with a clear message for the user.
- Keep the inspection rubric concrete: missing content, broken math, clipped text, unreadable tables, bad page breaks, citation/link problems, figure rendering, and obvious visual defects.
- Follow the app's existing task/progress conventions so ChatKit and browser users see drafting, compilation, rendering, inspection, retry, save, and download-ready states.
- Log meaningful report lifecycle events at service boundaries: report ID, source/library artifact IDs, export type, renderer, model used for inspection, duration, retry count, and outcome. Avoid logging report body text, prompts, secrets, or large rendered payloads.
- Keep failures allowed to propagate unless a deliberate user-facing recovery path exists; partial artifacts should either be saved with explicit status metadata or cleaned up by the task boundary.

Acceptance criteria:

- A ChatKit agent can create a structured report from selected library sources and save it as a library artifact.
- The same report can be rendered to Markdown with KaTeX-compatible math and evidence links intact.
- The report can be rendered to PDF, inspected from rendered page images by a vision model, improved if needed within a bounded retry loop, and saved back into the library.
- Browser and ChatKit progress accurately show compile/render/inspection/save states without noisy step-by-step logs.
- Download links point at saved library artifacts, not unmanaged temporary files.
- Integration tests cover report creation, Markdown rendering, PDF artifact saving, progress/status reporting, and library selection/search behavior. Unit tests cover only tricky render normalization or inspection-rubric parsing.

### 5. ChatKit Stability Verification

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

### 6. Browser Workflow Coverage

Status: ongoing.

Add Playwright coverage for normal file-library work:

- Create folder.
- Upload text/json/PDF.
- Navigate folders.
- Rename and move.
- Select files and ask ChatKit a grounded question.
- Reveal a source from ChatKit or Library view.
- Delete files and folders with progress/status feedback.

### 7. Usage Credits And Stripe-Ready Billing

Status: first backend pass implemented; admin UI, Stripe funding, and complete usage coverage remain planned.

Reference `../plodai` directly during implementation and align models/services/schemas with it where the concepts match, adapting names only where this app's library/task surfaces need richer references.

PlodAI reference setup:

- Clerk sign-up creates the account, but app access is still gated by manual activation.
- Clerk metadata carries `active`, `role`, and `credit_floor_usd`; activation sets a default negative floor so a newly activated user can try the product before payments exist.
- The database stores `user_credit_balances`, `credit_grants`, and `cost_events`.
- Admin endpoints and an admin UI list Clerk users, show activation/balance state, activate/deactivate users, and grant manual USD credit with an audit note.
- ChatKit usage is converted to a platform cost by applying a multiplier to OpenAI token/transcription pricing, accumulated into thread metadata, recorded as cost events, and deducted from the user's credit balance.
- Non-admin users are blocked when their current credit reaches the configured floor; admins bypass the paid-user gate.
- Stripe is not implemented there yet; the manual grant ledger is the bridge that can later receive payment-created credits.

Decision:

- Bring the same account monetization model here: manual activation first, free trial credit on activation, prepaid USD credit balances, cost-event debits, and a configurable markup over underlying OpenAI API cost.
- Start with a default markup in the `1.2x` to `1.5x` range, exposed as settings rather than hardcoded, so the business rule can change without a migration.
- Treat Stripe as a future credit-funding source, not as the first implementation dependency. The first pass should make the ledger, admin grant flow, and usage debiting correct even when all credits are granted manually.
- Cover every expensive OpenAI-backed path, not only ChatKit text turns: ChatKit agent runs, transcription/voice if enabled, image generation, research discovery/answers, source-file vector uploads/searches where measurable, and any future generation actions.

Implementation notes:

- Completed first pass: added `user_credit_balances`, `credit_grants`, and `cost_events` tables with migration coverage; added `BillingService` for grants, idempotent debits, cost calculations, and credit-floor checks; exposed `/api/billing/me`, `/api/admin/users`, `/api/admin/users/set-active`, and `/api/admin/credits/grant`; added current credit and floor fields to `/api/auth/me`; gated billable REST operations before long-running work; recorded ChatKit response usage as auditable cost events when response usage is available; and records QA/freeform action usage from REST, MCP, or ChatKit tool calls.
- Use these PlodAI files as the first implementation reference: `../plodai/backend/app/models/credit.py`, `../plodai/backend/app/models/credit_grant.py`, `../plodai/backend/app/models/cost.py`, `../plodai/backend/app/services/credit_service.py`, `../plodai/backend/app/core/clerk_metadata.py`, `../plodai/backend/app/core/auth.py`, `../plodai/backend/app/services/clerk_admin_service.py`, `../plodai/backend/app/schemas/credits.py`, `../plodai/backend/app/api/routes.py`, `../plodai/backend/app/chatkit/usage.py`, `../plodai/backend/app/chatkit/metadata.py`, `../plodai/frontend/src/components/AdminCreditsPanel.tsx`, and `../plodai/frontend/src/types/credits.ts`.
- Reuse this repo's existing Clerk and `AppUser` foundation: `private_metadata` already provides `active` and `role`, and local `AppUser` records already mirror Clerk identity.
- Align table shape and behavior with PlodAI's `UserCreditBalance`, `CreditGrant`, and `CostEvent` where possible so credit grants, balance debits, cost audit records, admin summaries, and future Stripe grants stay portable between the two projects.
- Add billing metadata support for a per-user credit floor, activation defaults, and admin-only active/role/balance management without weakening the local-dev auth path.
- Add app-owned billing tables with migrations and drift tests: current balance, manual/admin credit grants, cost events with user/thread/task/source/action/openai response IDs when available, pricing version, raw usage summary, platform multiplier, and optional Stripe/payment reference fields for later.
- Add a `BillingService` or equivalent boundary that can grant credits, compute marked-up cost, record idempotent debits, enforce balance floors, and expose concise balance/status responses to web, ChatKit, MCP, and tests.
- Centralize OpenAI pricing data and model it as configuration/versioned constants. Unknown models should log a warning and choose a deliberate policy: block, charge zero with an audit event, or require an explicit fallback price.
- Gate billable user operations before starting long work where possible, then record actual cost after completion using the logged response IDs and usage data already captured by the OpenAI/ChatKit observability layer.
- Add admin REST endpoints and a compact admin view for user search, activation/deactivation, manual credit grants with notes, balance display, recent grants/costs, and low/empty credit status.
- Keep user-facing billing light in the normal workspace: show current credit/remaining trial state and clear blocked-state copy, without adding Stripe checkout until the ledger is proven.
- Prepare Stripe integration by reserving fields for payment provider, checkout/session/payment intent IDs, payment status, and credit amount, then later add webhook-driven credit grants with idempotency.
- Next pass: record post-completion cost events for the remaining non-ChatKit OpenAI paths such as semantic split, research discovery, source vector indexing/search, image, voice, and transcription; add the compact admin UI; and add Stripe-created credit grants once the manual ledger has been exercised.

Acceptance criteria:

- A signed-up Clerk user remains blocked until manually activated.
- Activating a user grants or exposes a default free-usage allowance, after which the user can use normal library and ChatKit workflows.
- Billable OpenAI usage creates auditable cost events, debits the user's balance using the configured markup, and leaves enough identifiers to reconcile a charge against app logs and OpenAI platform logs.
- Users at or below their credit floor receive a clear payment/credit-required response before new expensive work starts.
- Admins can search users, activate/deactivate accounts, grant credit with a note, and inspect balances without direct database edits.
- Tests cover activation gating, admin grants, debit idempotency, cost calculations, low-credit blocking, local-dev/admin bypass behavior, and the contract surface exposed to the frontend.

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
- ChatKit can name threads early through a side-effect tool that updates thread title metadata.
- ChatKit tool responses are compacted to avoid passing OpenAI file IDs, full tag objects, raw metadata, and bulky result payloads back through conversation state.
- Tag slugs are treated as stable tag identifiers for ChatKit; backend tag-filter paths accept tag slugs as well as legacy tag IDs.

### Logging And Debugging

Status: complete for the current baseline.

- Backend OpenAI calls log response IDs, conversation IDs, request IDs, model/status, token totals, duration, and clickable platform log URLs.
- FastAPI request logs and framework logs reach the configured file log.
- `skills/openai-log-debugger` can fetch logged Responses and Conversations artifacts into `.local/openai-debug/`.
- Logs avoid prompts, secrets, and bulky response bodies.

### Browser UX Fixes

Status: mostly complete; view split first pass implemented.

- Hidden empty preview.
- Wider selected-file preview.
- Persisted explorer/chat splitter.
- In-app delete confirmation with delete progress.
- Recursive-folder delete warning.
- Arrow-key focus and Shift+arrow range selection.
- `F2`, `Backspace`, `Alt+Left`, `Delete`, and `?` shortcut help.
- Closable/resizable preview.
- Reduced active-task polling to a slower background cadence with targeted refreshes after actions.
- Explorer/Library tab split with local Explorer filtering and semantic Library search.

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
- `npm run test:e2e -- --grep "explorer local search"`
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
- Should ChatKit source annotations move beyond deeplink markdown once native custom output annotations are verified?
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
