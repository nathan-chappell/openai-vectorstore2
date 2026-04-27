# Plan: AI File Browser, ChatKit, And MCP Apps

## Purpose

Build a product-ready workspace for managing, searching, and using a personal research library.

The app has three coordinated surfaces:

1. Web app: a Vite/React file-library workspace with ChatKit beside it.
2. MCP: tools and MCP Apps resources exposing the same library capabilities.
3. App core: typed services, schemas, database models, and tests that keep REST, ChatKit, MCP, and frontend contracts aligned.

The product is file-library first. Ingestion stores the original source file, publishes that source file to an OpenAI vector store, and searches the source-level index with app-owned filters for tags, paths, source type, explicit source IDs, and dates. Semantic splitting remains available as an explicit inspection or re-split tool, but it is not part of normal ingestion.

## Wrap-Up Checklist For 1.0.0 Beta

Goal:

- Get the project to a polished, realistic demo state: a credible RAG system, a repeatable agentic workflow, advanced OpenAI API usage, and a live project that is strong enough to reference from a resume.
- Deploy it as a sister service to PlodAI with roughly the same production posture. Assume `../plodai` is good enough as the deployment reference and this project mainly needs its own database/storage/API environment values.
- Target beta-production readiness, not a large new roadmap. Finish the important proof points, document them, and avoid taking on nonessential product expansion before the first deployed version.

Release scope:

- Version starts at `1.0.0` for both Python and frontend package metadata.
- Primary demo path: upload/build a small research library, inspect files, run tag and semantic search, ask grounded questions with evidence, generate a structured report, render/preview/export it, and show logs/progress/cost tracking for OpenAI-backed work.
- The project should demonstrate source-level OpenAI vector-store RAG, ChatKit agent tools, MCP surface area, task/progress updates, generated artifacts, billing/usage accounting foundations, and deployable app architecture.
- Longer-term extension: demonstrate a credible on-prem mode where the app can run against a self-hosted OpenAI-compatible OSS model service instead of making OpenAI API calls for agent turns.

Functional final checks:

- Verify tag filtering and semantic Library search with a realistic library: all/any tag mode, nonblank fallback query, source metadata display, explicit `@` file references, file reveal, and evidence links.
- Verify grounded ChatKit answers from explicit Explorer/Library file references and Library search results, including citation clicks and browser-side reveal behavior.
- Verify research-builder flow on a small topic: discovery, ingest, vector indexing, scoped answer, progress visibility, and log traceability.
- Verify report generation end to end: structured draft, Markdown render with KaTeX-compatible math/evidence links, saved library artifact, PDF render path when implemented, PDF inspection/retry behavior, and download links that point at saved artifacts.
- Verify deployed MCP from the user's ChatGPT account: connect to the deployed MCP server, run exposed tools/resources from ChatGPT, and confirm the MCP Apps UI renders well enough for screenshots.
- Completed setup for FastMCP dev-server workflow: added `backend/app/mcp/dev_server.py:mcp`, documented Apps and Inspector commands, and covered the dev server tool surface in contract tests. Still run it manually against a realistic local library before deployment to exercise MCP tool discovery, Apps UI resources, research actions, semantic/tag search, source detail views, and raw-file/content retrieval.
- Verify generated assets and stored artifacts are reachable from the library, selectable for ChatKit context where appropriate, and covered by cleanup/delete flows.
- Verify billing/usage foundations are acceptable for beta: activation gate, admin credit grant, low-credit block, cost event creation for the main expensive paths, and clear logs with response/conversation IDs.
- Run the standard verification suite plus at least one browser smoke pass against a seeded realistic library.

Deployment checklist:

- Completed Docker image pass: added a Dockerfile that builds the Vite frontend, installs the Python app runtime, copies migrations, exposes port `8000`, and starts `openai-vectorstore2-http`; migrations are documented as a pre-start command for deployment.
- Completed `.dockerignore` pass: excludes `.venv`, `node_modules`, frontend build output, local logs/storage, test/debug artifacts, caches, databases, Git metadata, VS Code config, and secrets.
- Completed docs pass: added Docker/Railway deployment notes covering service start command, health check, required env vars, storage choice, migration workflow, logs, admin integration, billing defaults, and image build/push commands.
- Prefer Docker deploys for Railway. Publish images as `nathanschappell/openai-vectorstore2:1.0.0` and later tags with `docker push nathanschappell/openai-vectorstore2:tagname`.
- Provision a Railway Postgres database if it can sleep or otherwise fits the beta budget. Match PlodAI's deployment pattern where possible and switch this app's DB env vars to the new service.
- Completed docs pass for mandatory env vars: `OPENAI_API_KEY`, app signing secret, database URL, app base URL, Clerk values when auth is enabled, storage backend/S3-compatible values, billing/admin settings, and ChatKit domain key are documented in `.env.example` and `docs/deployment.md`.
- Decide whether beta admin/auth/payments come from this repo's default implementation or the private shared `ai-portfolio-admin` submodule; either path must leave the app bootable and demoable.
- Track the private on-prem companion repo as `vendor/openai-vectorstore2-on-prem`, sourced from `git@github.com:nathan-chappell/openai-vectorstore2-on-prem.git`, for RunPod/SGLang/fine-tuning work that should not live in the base app until the boundary is proven.
- Decide beta storage explicitly: local container storage is acceptable only for throwaway demos; persistent Railway volume or S3-compatible storage is preferred for a live resume link.
- Confirm logs work in Railway without leaking prompts, secrets, or bulky content, and that enough IDs are present to debug OpenAI API calls from platform logs.

Resume/demo checklist:

- Update README with a concise feature list, architecture diagram or section, local run commands, Docker/Railway deployment notes, and a demo script.
- Add screenshots or a short walkthrough showing Explorer, Library semantic search, ChatKit grounded answer, report artifact, MCP Apps UI, and deployment/runtime logs if useful.
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
- Completed auth/admin payload cleanup: Clerk user and email JSON now enters through typed-dict boundary parsing, and user/admin record construction uses typed metadata accessors for active state, role, and credit floor instead of repeated bare dict handling.
- Completed public schema metadata narrowing: source detail metadata and research candidate provenance now reuse the shared typed-dict contracts with narrow default factories instead of broad `dict[str, Any]` fields.
- Be careful with method covariance and invariant containers: build with narrow local types, then assign or cast at the final wider return/override boundary when required.
- Remove helpers/normalizers made redundant by stronger typed boundaries, and drop legacy chunk/vector compatibility code where tests show the current source-level indexing flow no longer depends on it.
- Keep edits incremental with integration-level coverage; add unit tests only for tricky parsing or normalization logic that remains.

Acceptance criteria:

- Pyright stays clean without `type: ignore`.
- Common JSON shapes are named and reused instead of re-declared as `dict[str, object]` throughout services.
- SQLAlchemy JSON payloads are read/written through typed properties where practical.
- Removed legacy code has either no remaining call sites or a current test that proves the replacement path.

### 1. TypeScript Contract And Legacy Cleanup

Status: complete for current frontend refactor baseline; continue only opportunistic cleanup.

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
- Completed API contract pass: named repeated frontend request parameter shapes and reused filter/query serializers at the API boundary.
- Completed ChatKit contract pass: named ChatKit metadata, deeplink, and client-tool result shapes instead of repeating broad inline records.
- Completed coverage pass: expanded browser coverage for Library tag-filter payloads and opening a semantic result back into Explorer with preview.
- Inventory repeated TypeScript shapes for sources, explorer entries, library results, tags, task status/progress, ChatKit tool payloads, billing summaries, generated assets, and future report artifacts as new surfaces are added.
- Future cleanup should be opportunistic: extract hooks only if `App.tsx` orchestration becomes harder to maintain, and keep new API boundary payloads named as features are introduced.
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
- Library view should focus on filtering and discovery: query, AI-managed tags, type/date filters, result summaries, and source metadata. Tag filters can be visible here, but manual tag creation/editing should stay out of the normal user UI unless a later admin-only cleanup flow proves necessary.
- ChatKit should work with both views through composer `@` file references, citation links, and reveal/search client tools rather than a persistent selected-file retrieval scope.

Implementation notes:

- Completed first pass: added Explorer/Library tabs, local current-folder fuzzy filtering for Explorer, a semantic Library query surface with tag pills and `all` / `any` mode, Enter replace search, Ctrl+Enter append/dedupe search, ChatKit `set_file_search` routing to Library, and fuzzy ChatKit composer entity search over known file entries.
- Keep the existing virtual filesystem as the source of truth for folders and paths.
- Explorer search is local to the current folder and should be simple/fuzzy over entry name, path, description, summary, suggested tags, and source metadata that is already present in the folder listing. It must not call OpenAI vector search.
- Reuse the same fuzzy matching behavior for ChatKit composer entity search so file tags feel consistent with the Explorer.
- Library view is tag/semantic-search focused: start from a nonblank fallback query when the field is empty, show tags as pill-style multi-select controls, support `all` / `any` tag matching, and run OpenAI-backed source search from the query bar.
- In Library view, `Enter` replaces the current result set and `Ctrl+Enter` appends/dedupes into it.
- Results from Library view should preview in place and remain discoverable through ChatKit `@` file references; explicit citation/entity clicks should still reveal/open the corresponding file in Explorer.
- ChatKit `set_file_search` should move the user to Library view and apply query/tag filters instead of crowding Explorer.
- Refine Explorer shortcut behavior around simple arrows: Up/Down move file focus/selection, Shift+Up/Down extends selection, Left/Right navigate backward/forward through a stack-based folder path history, `F2` renames, and `Delete` deletes.
- Keep a simple folder-history stack for Explorer navigation instead of treating backward navigation as "go to parent folder"; entering folders, revealing files, and ChatKit/Library-driven folder jumps should push usable history entries without creating loops.
- Keep preview closable and resizable so file details stay readable.

Next:

- Completed follow-up: Library rows fall back to vector-result attributes such as `virtual_name` and `virtual_path` when a semantic hit is not already in the local entry cache.
- Completed follow-up: removed the confusing visible chat-scope file selection path; ChatKit now relies on `@` file references and a rolling entity-search history of currently/recently shown files.
- Completed follow-up: add-file, split-preview, and research-builder workflows moved out of the Explorer pane and into ChatKit starter prompts/attachments/tooling, leaving Explorer and Library less crowded.
- Completed follow-up: Library tag filtering now caps the default visible pill set, prioritizes selected/relevant tags, reruns the current Library search when tags are clicked, and asks semantic splitting to produce fewer broad auto-tags.
- Completed follow-up: Explorer Left/Right folder-history navigation is wired and covered by the desktop browser shortcut spec; Backspace/Alt+Left still navigate to the parent folder.
- Completed follow-up: Explorer and Library rows now use denser name-first columns with a compact type/icon column, size/date/relevance context, and status/relevance near the end instead of path subtitles under every row.
- Completed follow-up: full path/context moved into the preview/details pane, including a dedicated path metadata field.
- Completed follow-up: Markdown source previews now render through a scoped Markdown preview renderer with compact headings, lists, code blocks, tables, links, and paragraph spacing.
- Completed follow-up: visible manual tag creation/editing was removed from normal Explorer/Library flows; tags are now displayed and filtered as AI-managed metadata.
- Consider a later admin tag-cleanup view for merge/delete/rename workflows if realistic libraries still accumulate noisy tags after generation limits.

Acceptance criteria:

- Explorer no longer needs prominent tag chips or dense search controls to feel complete.
- Explorer rows show name-first, compact type/icon, size/date as useful, and status last; long paths do not crowd the row or hide important table columns.
- Source previews expose the full path and metadata clearly enough that removing per-row path subtitles does not reduce inspection quality.
- Markdown previews render through the app renderer with scoped styles that look professional inside the split preview pane and do not inherit oversized document defaults.
- Explorer search filters only the current folder and remains responsive without billing or OpenAI calls.
- Library filtering has enough space to show tags, query, result status, and source metadata clearly.
- Normal users cannot manually add tags from the Explorer/Library UI; tags remain AI-managed metadata while tag filters and tag visibility still support discovery.
- Tag pills in Library stay readable with large tag vocabularies, selected tags remain visible, and clicking a tag immediately refreshes the Library result set.
- Auto-generated tags are bounded and broad enough for retrieval filtering rather than creating many narrow one-off topic tags.
- Empty Library query submissions use a deliberate fallback query rather than sending a blank API request.
- `Enter` and `Ctrl+Enter` semantics are covered in the browser UI and do not disturb ChatKit entity-reference behavior.
- Revealing a file from ChatKit opens the correct file in Explorer; clicking a Library result previews it in place without stealing Explorer focus.
- Explorer hotkeys work when focus is inside the file list rows, including after mouse selection.
- Explorer shortcut help, such as `frontend/src/components/ExplorerDialogs.tsx`, documents Up/Down selection and Left/Right folder-history navigation rather than the older parent-folder-only behavior.
- Folder history is deterministic: opening a folder pushes history, Left goes back, Right goes forward, and new navigation after going back truncates forward history.
- Playwright covers switching views, tag filtering, file reveal, entity reference, and preview behavior.

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

Status: Markdown model, renderer, source persistence, REST, ChatKit, and MCP paths implemented; PDF export remains planned.

Goal:

- Agents should be able to create, revise, persist, preview, and export report documents from library context.
- A report should be stored as a loosely structured document that maps naturally to Markdown and supports math notation such as KaTeX.
- Reports should be first-class library artifacts: creation writes the structured report into the library, and generated previews/exports are saved back into the library before download.

Implementation notes:

- Completed first pass: added a Pydantic structured report document model for title, sections, prose blocks, lists, tables, citations/evidence links, equations/math blocks, and figures/assets.
- Completed first pass: added a canonical Markdown renderer that preserves citation links, KaTeX-compatible display/inline math, escaped tables, lists, figures, and references.
- Completed first persistence/API pass: added a typed `POST /api/reports/markdown` boundary that renders a structured report to Markdown, saves it through canonical source ingestion, stores report metadata on the resulting source, returns the source/task, and exposes matching frontend contracts.
- Completed agent tool pass: ChatKit and MCP now expose `save_report_markdown`, using the same typed structured report request and canonical source-ingestion save path as REST.
- Later pass: optionally render PDF from the same structured report source.
- Store compiled report artifacts through the same library/storage boundaries used for source files and generated assets, so reports can be searched, referenced from ChatKit, previewed, downloaded, and cited later.
- ChatKit should expose agent tools to draft/update a structured report, compile it, save it into a folder, render Markdown preview, render PDF preview, and return library links to the saved artifacts.
- PDF download should normally mean "render PDF, save it in the library, then offer the saved library artifact for download" rather than producing an unmanaged transient file.
- Add a PDF inspection loop for the PDF-producing agent: render PDF pages to images with `pdf-lib`, `pdf.js`, or an equivalent renderer; send page images to a 5.4-class vision model for layout/content critique; if the critique is good enough, publish the PDF, otherwise revise and re-render once or twice before returning the best PDF with a clear message for the user.
- Keep the inspection rubric concrete: missing content, broken math, clipped text, unreadable tables, bad page breaks, citation/link problems, figure rendering, and obvious visual defects.
- Follow the app's existing task/progress conventions so ChatKit and browser users see drafting, compilation, rendering, inspection, retry, save, and download-ready states.
- Log meaningful report lifecycle events at service boundaries: report ID, source/library artifact IDs, export type, renderer, model used for inspection, duration, retry count, and outcome. Avoid logging report body text, prompts, secrets, or large rendered payloads.
- Keep failures allowed to propagate unless a deliberate user-facing recovery path exists; partial artifacts should either be saved with explicit status metadata or cleaned up by the task boundary.

Acceptance criteria:

- A ChatKit agent can create a structured report from referenced library sources and save it as a library artifact.
- The same report can be rendered to Markdown with KaTeX-compatible math and evidence links intact.
- The report can be rendered to PDF, inspected from rendered page images by a vision model, improved if needed within a bounded retry loop, and saved back into the library.
- Browser and ChatKit progress accurately show compile/render/inspection/save states without noisy step-by-step logs.
- Download links point at saved library artifacts, not unmanaged temporary files.
- Integration tests cover report creation, Markdown rendering, PDF artifact saving, progress/status reporting, and library search/entity behavior. Unit tests cover only tricky render normalization or inspection-rubric parsing.

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

### 7. On-Prem OSS Model Support

Status: first compatibility foundation implemented; private companion repo initialized as `vendor/openai-vectorstore2-on-prem`; first RunPod operator skeleton added and locally smoke-checked.

Goal:

- Generalize the app so agentic workflows can run either through OpenAI Responses/Conversations or through an on-prem-style OpenAI-compatible model endpoint.
- For this project, "on-prem" means deployed on infrastructure under our control, likely Railway for the app/database and RunPod for model serving/training, without external model API calls for the agent path.
- Target `gpt-oss-20b` first as the practical small OSS model. An A100-class GPU should be the initial target for LoRA SFT and SGLang serving, with smoke tests confirming memory/runtime settings before serious runs.
- Keep the base repo focused on provider abstraction, contracts, and compatibility mode; keep RunPod images, SFT scripts, SGLang launchers, model caches, and fine-tune artifacts in the private on-prem submodule.

Feasibility notes:

- OpenAI's gpt-oss models are open-weight and intended for infrastructure controlled by the developer; they are not served through the OpenAI API or ChatGPT, and fine-tuning uses open tooling rather than OpenAI API fine-tuning.
- `gpt-oss-20b` is the right first target for this repo's on-prem story because it is the lower-latency/local/specialized variant and supports agentic capabilities such as function calling and structured outputs.
- SGLang is a plausible serving layer because it supports GPT-OSS models, OpenAI-compatible API surfaces, reasoning/tool parsers, and MCP tool-server integration.
- The OpenAI-direct path should remain the highest-capability default. On-prem mode is a compatibility/deployment track, not a reason to give up Responses/Conversations when those are available.

Submodule structure:

- Use `vendor/openai-vectorstore2-on-prem` from `git@github.com:nathan-chappell/openai-vectorstore2-on-prem.git` as the private companion repo.
- The submodule may assume the parent checkout exists and provides the main app files, schemas, tool contracts, prompts, logs, and fixtures.
- Keep private deployment and model work there: RunPod Dockerfiles, bootstrap scripts, SGLang launch manifests, model download/cache conventions, SFT dataset builders, training scripts, eval harnesses, and artifact exfiltration helpers.
- Keep the parent-to-submodule interface narrow: provider contracts, prompt/tool schemas, dataset/export inputs, and generated artifacts. Avoid making the base app depend on submodule imports at runtime.

Provider/agent architecture:

- Completed first pass: added `chat_completions_v1` runtime settings, a typed OpenAI-compatible chat-completions gateway boundary, known model context-window resolution, 75/25-style context-compaction budget helpers, a simple POST web-search gateway contract, and tests for model context/summary behavior.
- Completed routing foundation: ChatKit can choose the Agents SDK `OpenAIChatCompletionsModel` when `AGENT_MODEL_PROVIDER=chat_completions_v1`, while `openai_responses` remains the default Responses/Conversations path. The compatibility route uses app-owned active history instead of OpenAI conversation IDs and avoids direct OpenAI file attachments.
- Add an app-level model-provider abstraction for agent turns with at least two implementations: `openai_responses` and `chat_completions_v1`.
- Preserve the current OpenAI Responses/Conversations ChatKit implementation for best capability, tracing, server-side state, and advanced tool use.
- Add a `chat_completions_v1` compatibility mode for the widely supported `/v1/chat/completions` message/tool API surface. This mode must work with OpenAI chat-completions models and with `oss-small`/`gpt-oss-20b` served through SGLang or the smallest/easiest compatible serving option if SGLang is not viable.
- Use known context sizes only. Compatibility mode may assume either an OpenAI model with known limits or the configured OSS model served by SGLang; unknown model/context combinations should be blocked or require explicit config rather than guessed.
- Compatibility mode should support messages, streaming when available, tool definitions, tool call parsing, structured output where practical, and explicit conversation-state storage in the app.
- ChatKit should be able to route through either provider while preserving app-owned progress events, tool execution boundaries, explicit source-reference behavior, citation/link behavior, and cost/log metadata where applicable.
- Tool calling should be normalized at the app boundary: the model provider proposes tool calls; app services execute them; results are serialized back in a provider-compatible shape.
- Add clear capability flags so on-prem mode can expose unsupported features honestly, such as pgvector local retrieval instead of managed vector stores, weaker structured-output enforcement, different reasoning traces, and deferred image/voice/transcription features.
- Add a smoke harness that can route ChatKit agent turns through `chat_completions_v1` using OpenAI chat-completions first, then the configured OSS/SGLang endpoint.

Conversation-state and context compaction:

- Completed first pass: added ChatKit entry visibility/compaction metadata and a store method that marks compacted items without deleting original payloads, inserts a summary item before the remaining active segment, and keeps active history queries from replaying compacted entries.
- `chat_completions_v1` owns conversation state in the app database because the API surface is stateless relative to Responses/Conversations.
- Configure context windows per supported model. Start with enough OpenAI chat-completions models plus `oss-small`/`gpt-oss-20b` to cover at least 10 models, preferably around 20 when the model table is available.
- Keep the compaction policy simple and relative to known context size. Default to a rough `75/25` policy: preserve the newest conversational/tool context until about 25% of the model context remains, then compress roughly the oldest half of the retained history into a compact summary and continue from that new segment.
- Reserve a reasonable output budget before calculating history capacity. The exact formula is less important than keeping enough room for model output and avoiding context-limit failures.
- Before writing the final summary, optionally ask the active model for compaction notes: what facts, source IDs, tool outputs, user preferences, unresolved tasks, and constraints are still meaningful. Then produce a concise summary from those notes.
- The summary shape should be explicit enough for later training and debugging. Start with sections such as `Data`, `Conversation`, and `Remarks`, and include source/tool IDs as compact markdown lists where those IDs remain useful.
- Tool calls and raw tool results do not need to be perfectly preserved after compaction, but relevant structured data should be smartly rendered into compact human/model-readable summaries, such as id/name/description/source-status bullets.
- On-prem mode should perform its own compaction with the active on-prem provider. Include representative compaction examples in the dataset-builder path if the task proves difficult enough to benefit from fine-tuning.
- ChatKit storage should support hiding or soft-deleting compacted thread items and inserting a completion summary at the beginning of the new visible history segment. Preserve originals according to the app's retention needs so debugging and dataset generation remain possible.

Compatibility-mode retrieval and web search:

- Completed first pass: documented and configured the web-search POST URL and added a typed response parser that accepts query/results/summary payloads.
- Managed OpenAI vector-store search must become a provider implementation rather than a hard dependency for agent turns.
- Add a local retrieval provider over pgvector for on-prem/compatibility mode. Investigate whether `oss-small`/`gpt-oss-20b` can be used effectively as an embedder; if not, document the blocker and choose a separate local embedding path before treating on-prem RAG as viable.
- Add a web-search tool implementation for `chat_completions_v1`. It should call a configured URL with `POST` and a simple typed payload containing natural-language input and any small search options we need. The response should return web-search-compatible results that can be summarized by the agent and saved into the library.
- Keep search results saveable as normal library sources/artifacts so later retrieval, citation, and report flows work the same way as uploaded/generated sources.

On-prem feature boundary:

- For the first compatibility pass, leave image generation, voice generation, transcription, and PDF vision inspection out of on-prem mode, but mark TODOs where provider implementations should attach later.
- Transcription should be feasible later through Whisper or an equivalent local service.
- Image/multimodal features may become feasible through OSS model capabilities, but should wait until `oss-small`/`gpt-oss-20b` runtime quality is actually tested.
- Cost/billing for on-prem model calls should be configurable by environment. Until real costing exists, default to a placeholder rate of `$1.00` per million tokens so billing and usage paths continue to exercise normally.

Fine-tuning and dataset builder:

- Add a local skill or script workflow for building SFT examples from existing OpenAI platform logs, response logs, and stored conversation artifacts.
- The dataset builder should convert multi-turn conversations into one or more supervised examples, controlled by parameters such as window size, target assistant turns, include/exclude tool calls, include system/developer messages, and redact/scrub sensitive fields.
- Examples should preserve the production shape as much as possible: developer instructions, user request, referenced context, tool calls/results, final assistant answer, and metadata about source workflow.
- Include `chat_completions_v1` examples, web-search examples, local retrieval examples, and context-compaction examples when those workflows become representative enough to train from.
- Build a small subjective eval set first: roughly 20 interesting examples, with about 5 "showcase" examples that demonstrate known-good behavior and about 15 held-out/generalization examples that are not used for training feedback.
- The eval should be intentionally subjective and product-centered: quality of agent behavior, tool-use choices, evidence/citation behavior, refusal/defer behavior, report/search usefulness, and whether the answer feels like this app's agent.
- Keep eval examples and rubrics versioned. Use model-graded critique only as a helper; final acceptability should be based on human review for the initial small set.
- SFT should start with LoRA/QLoRA style training in BF16 or 4-bit where supported, then serve either base+adapter if SGLang supports it cleanly or merge/export a deployment artifact after training.
- Do not train directly in deployment quantization formats unless the toolchain explicitly supports it; use a BF16/LoRA training path and quantize/export only after adapter merge when needed.

RunPod workflow conventions:

- Follow `../runpod-nlsh` as the working inspiration: choose a RunPod base image aligned to the target GPU, CUDA, PyTorch, and attention/runtime stack; bake stable system and Python dependencies into the image where possible; use `/workspace` as a mounted volume for caches, model weights, checkpoints, state, and artifacts.
- Prefer letting RunPod download model parameters and install/cache heavy runtime pieces on the mounted volume rather than pulling massive artifacts locally.
- Treat the mounted volume as durable-ish cache, not source of truth. Every serious run should write enough manifests, logs, state files, and artifact summaries to resume or exfiltrate with `scp`.
- Use a strict venv convention on the mounted volume when runtime package iteration is needed, while keeping the base image stable for CUDA/PyTorch/SGLang compatibility.
- Bootstrap should be stdlib-light, create cache directories such as Hugging Face cache, SGLang storage, Triton cache, temp, checkpoints, and artifacts, start RunPod base services through `/start.sh` when present, then hand off to a typed workflow CLI.
- Workflow should support dry-run, download-only, baseline-eval, train, serve, post-train-eval, and exit-or-stay-alive modes.
- Artifacts to exfiltrate should include dataset snapshots, eval reports, critique logs, adapter/checkpoint metadata, SGLang launch config, server logs, and a small reproducibility manifest.

Pod-ready implementation slice:

- [x] Add the first executable skeleton to `vendor/openai-vectorstore2-on-prem`: `docs/`, `skills/`, `scripts/`, `configs/`, `requirements/`, `artifacts/.gitkeep`, and a README update that names the parent-app boundary and default `/workspace` layout.
- [x] Add a stdlib-light RunPod bootstrap script that creates `/workspace/hf-cache`, `/workspace/sglang-storage`, `/workspace/triton-cache`, `/workspace/tmp`, `/workspace/venvs/openai-vectorstore2-on-prem`, `/workspace/datasets`, `/workspace/checkpoints`, `/workspace/adapters`, `/workspace/evals`, `/workspace/exfil`, and `/workspace/logs`, writes a small initialization manifest, and can stay alive for operator action.
- [x] Add RunPod operator docs and env examples covering pod host, SSH port, key path, workspace path, Hugging Face token/cache vars, model ID, dataset paths, SGLang port, and the live smoke path for parent app compatibility wiring.
- [x] Add the first skill/operator guides for `runpod-connect`, `runpod-inspect`, `dataset-sync`, `sglang-control`, `sft-run`, `eval-run`, and `artifact-exfiltrate`, with transparent SSH/SCP commands, non-mutating health checks by default, and env-var based configuration rather than hidden state.
- [x] Add first-pass SGLang launch profiles for `openai/gpt-oss-20b`: base-only and base-plus-adapter profiles with conservative A100-oriented settings, plus start, stop, restart, status, health, and tail-log commands. Still tune aggressive profiles after a real smoke run.
- [x] Add first-pass dataset sync conventions that upload/download dataset directories into `/workspace/datasets/<version>`. Still add overwrite guards and explicit train/eval split validation once the dataset builder writes real split manifests.
- [x] Add first-pass weight/artifact conventions that prefer RunPod-side base model downloads into `/workspace/hf-cache` and keep adapter paths under `/workspace/adapters`. Still add explicit `du -sh` recording and local-adapter upload helpers.
- [x] Add a first-pass exfiltration command that packages adapters, eval reports, selected logs, manifests, and `/workspace/exfil` for SCP back to the local machine. Still add package/version summaries, GPU/hardware summaries, git SHAs, and a richer reproducibility manifest.
- [x] Add a dataset-builder dry-run path that inventories likely parent-app OpenAI/ChatKit artifacts and writes a reviewable manifest without requiring a RunPod pod. Still turn this into candidate/train/eval/rejected JSONL generation with redaction and split controls.
- [x] Add a first eval harness that can run smoke prompts against a base SGLang or adapter endpoint through `/v1/chat/completions`, saving outputs and raw response bodies. Still add the full 20-example subjective eval set with showcase versus held-out labels and critique notes.
- [x] Add a first SFT runner that validates curated chat-message JSONL in dry-run mode and can launch a small PEFT/TRL LoRA smoke train on a CUDA-capable pod after `requirements/train.txt` is installed. Still run it on the live pod with real curated examples before claiming train/update readiness.
- [ ] Only after the pod-ready skeleton works locally and live RunPod connection values are available, run a real RunPod smoke sequence: bootstrap pod, inspect GPU/disk/venv/cache state, launch base SGLang, point the parent app at the OpenAI-compatible endpoint, run one ChatKit compatibility smoke turn, upload a tiny dataset, run a tiny SFT/eval command if feasible, and exfiltrate the resulting manifest/log bundle.

Acceptance criteria:

- The on-prem submodule is registered at `vendor/openai-vectorstore2-on-prem` and has a README explaining that it assumes the parent app checkout.
- The base app has a documented provider boundary between OpenAI Responses/Conversations and `chat_completions_v1`.
- ChatKit can run a smoke agent flow through `chat_completions_v1` using OpenAI chat completions without losing app-owned tool execution, progress, explicit source-reference behavior, citation links, or context compaction.
- `chat_completions_v1` can compact long app-owned history according to the configured model context size, soft-hide or otherwise retire old ChatKit items, and inject a `Data` / `Conversation` / `Remarks` summary into the new active segment.
- The compatibility provider can use a configured web-search POST endpoint and save useful search results back into the library.
- On-prem/compatibility retrieval has a pgvector implementation plan or implementation, including a decision on whether `oss-small`/`gpt-oss-20b` can serve as an embedder.
- On-prem usage accounting works with env-configurable placeholder token pricing, defaulting to `$1.00` per million tokens.
- The submodule contains a RunPod plan/script scaffold for SGLang serving of `gpt-oss-20b` and a dataset/eval workflow inspired by `../runpod-nlsh`.
- A dataset-builder skill/script can produce reviewable SFT examples from logged OpenAI conversations with redaction and multi-turn example splitting.
- A 20-example subjective eval set exists before fine-tuning work is considered successful.

### 8. Shared Admin, Auth, And Payments Submodule

Status: shared submodule foundation and host admin UI wiring complete; PayPal receipt temporary-credit flow implemented; free-credit request storage and full provider checkout/webhook payments remain planned.

Goal:

- Extract common sign-up, login, auth, admin-user management, credit/billing, and payment-provider code into a private shared project that PlodAI and this repo can consume.
- Use `git@github.com:nathan-chappell/ai-portfolio-admin.git` as the private source of truth, likely mounted as a git submodule in each portfolio app.
- Keep sensitive/admin implementation details out of the public-facing project repos while still preserving a clear boundary and a default provider path that lets each repo run without private production payment/admin wiring.
- Support payments through a provider-neutral boundary, with PayPal as an important planned provider and Stripe still possible later.
- Support a "request free credits" flow for early access, beta testers, and trusted networks such as LinkedIn connections. Admins must be able to approve, reject, and manually grant credits with clear audit history even before payment providers are fully automated.

Decision:

- Treat `ai-portfolio-admin` as a shared admin/auth/payments package, not as business-domain code. This repo should keep vector-store/RAG/library/report logic local.
- Each app should expose a narrow integration layer: current user, role/active state, credit balance, admin user operations, payment checkout/start, payment webhook/confirmation, credit grant, and cost debit.
- Free-credit requests are a first-class admin workflow, not a hidden manual database operation. They should produce request records, decision records, and eventual credit grants with source/reason metadata.
- The private submodule can provide the production implementation for Clerk, admin panels, credit ledgers, and payment providers.
- This repo must also provide a default/local provider path: local-dev auth, manual activation/credit grants, billing status, and clear "payments unavailable" behavior.
- The fallback boundary is important both for developer experience and for making the app understandable when private production payment/admin wiring is not configured. A fresh clone still needs `git submodule update --init --recursive` because shared contracts are now a declared dependency.

Completed:

- Completed first adapter pass: added app settings for `ADMIN_INTEGRATION_PROVIDER` and `ADMIN_SHARED_MODULE`, introduced `backend.app.admin` as the only public-app import boundary for private admin/auth/payment code, wired service bootstrap through that boundary, exposed typed payment integration status through `/api/billing/payment-status`, and documented the default public behavior versus private `ai-portfolio-admin` setup.
- Completed shared package scaffold: `../ai-portfolio-admin` now has typed Python contracts, Clerk metadata helpers, free-credit policy evaluation, PayPal receipt-review policy, an admin credit workflow protocol, reusable frontend TypeScript contracts/components, tests, and docs.
- Completed submodule dependency pass: this repo and PlodAI now mount `git@github.com:nathan-chappell/ai-portfolio-admin.git` at `vendor/ai-portfolio-admin`; Python metadata points at that local package; this repo's shared-adapter test loads the submodule path; and PlodAI's duplicated Clerk metadata/`UserRole` helpers now delegate to the shared package while preserving its current API surface.
- Completed circularity cleanup: `ai-portfolio-admin` no longer imports host apps. Host-specific adapters live in each host repo; this repo uses `backend.app.admin.shared_adapter`.
- Completed shared contract/interface foundation: shared package now owns generic user/admin/credit/free-credit/payment receipt contracts, Clerk metadata helpers, policy evaluators, a credit workflow protocol, and callback-driven frontend admin types/components.
- Completed shared admin UI wiring: `ai-portfolio-admin` now owns the callback-driven admin panel for user search, activation/deactivation, manual credit grants, free-credit review, and payment-attempt review; this repo mounts it through `frontend/src/components/AdminWorkspacePanel.tsx`; PlodAI mounts the same panel through its admin page and removed its duplicated local credit-panel implementation.

Remaining implementation plan:

- Completed PayPal receipt pass: users can create a PayPal payment reference, upload text/PDF/email-style receipt evidence, receive temporary credit when amount/currency/recipient/reference checks pass, and admins can review/confirm/reject payment attempts from the shared admin panel.
- Extend host endpoints for the shared panel's free-credit review callbacks once persistence exists. User search, activation/deactivation, manual credit grants, and payment-attempt review are wired now.
- Add host-owned persistence for free-credit requests: requester identity, requested amount, reason, source channel, optional LinkedIn/profile evidence, status, decision note, reviewer, timestamps, resulting credit grant ID, idempotency key, and duplicate/active-request checks.
- Use shared free-credit policy evaluation from `ai-portfolio-admin` in host endpoints. The shared package decides from typed evidence and policy; host apps own persistence and external evidence verification.
- Keep app-specific billing events local where they refer to source IDs, thread IDs, task IDs, report IDs, OpenAI response IDs, and vector-store operations; pass those as metadata into the shared credit/cost boundary.
- Expand host-local adapters only where they compose local services with shared contracts. Do not add host imports back into `ai-portfolio-admin`.
- Keep database ownership explicit. If shared admin tables are introduced, decide whether migrations live in `ai-portfolio-admin` and are included by host apps, or whether host apps vendor the table definitions into their own Alembic migration stream.
- Add/update submodule setup docs with the private URL, clone command, and `git submodule update --init --recursive`.
- Document clone/submodule setup and how the default provider behaves without private production wiring: auth mode options, disabled payment checkout, manual/admin credit grant fallback, and test fixtures.
- Add a provider-neutral payment lifecycle: create checkout/payment request, receive provider callback/webhook, verify provider event, idempotently grant credits, record provider IDs/status, and expose user-facing balance updates.
- Continue hardening the implemented PayPal receipt-based temporary access provider with expiry/revocation enforcement, richer receipt extraction, attempt limits, and stronger duplicate evidence checks.
- Extend the manual credit grant flow so grants are auditable for all users with admin ID, amount, note, source, optional payment/free-credit request reference, and resulting balance.
- Continue reserving fields for Stripe or other providers, but do not bake provider-specific names into core credit/cost tables unless they are in a provider metadata payload.

Free-credit request workflow:

- User-facing flow: submit a short request for trial/beta credits with optional LinkedIn profile URL, relationship note, intended use, and requested amount. Empty or casual requests should still be reviewable rather than discarded.
- Admin flow: list pending requests, inspect user identity/current balance/recent grant history, approve for a specific amount, reject with an internal/public note, or mark as manual review.
- Automatic policy flow: configured trusted request kinds can auto-approve a bounded credit amount. Example: verified LinkedIn connection evidence grants `$5.00` once per account. The package should model the rule and decision; the host app supplies evidence verification.
- Audit requirements: every request, auto decision, manual decision, grant, rejection, and expiry must preserve actor, timestamp, status, amount, source, note, and external references when available.
- Abuse controls: one active request at a time per user unless an admin overrides, configurable per-source maximum amount, idempotency keys for auto-grants, and reusable duplicate detection hooks.
- MVP scope: typed contracts, decision service, policy evaluator, admin review component, manual approval/rejection callbacks, and docs.
- Out of MVP scope: direct LinkedIn API integration, automated social graph verification, public marketing pages, subscription billing, and tax/invoice handling.

PayPal receipt-based temporary access:

- Goal: let a user pay externally through PayPal, upload proof of payment, and receive temporary access if the receipt plausibly matches the expected payment. Final payment truth must still come from PayPal-side data or admin confirmation.
- User flow: create pending payment attempt; show amount, currency, recipient PayPal account/payment link, and unique reference code; accept uploaded receipt/screenshot/PDF/email confirmation; run AI receipt review; grant temporary access only when policy passes; expire temporary access automatically unless reconciled; let admin confirm, reject, flag, extend, or revoke.
- Access statuses: `pending_payment`, `temporarily_approved`, `confirmed_paid`, `rejected_payment`, `expired_temporary_access`, and `manual_review_required`.
- AI receipt review should return structured data, not prose: amount, currency, payment date/time, PayPal transaction ID when present, payer name/email, recipient name/email, payment note/reference code, whether it appears to be a PayPal receipt, mismatch flags, tampering/suspicion flags, confidence/risk level, and a decision recommendation.
- Treat AI as evidence extractor and provisional gatekeeper only. It may grant temporary access under narrow policy; permanent access requires admin approval, PayPal-side verification, or future checkout/webhook confirmation.
- Temporary approval requires matching expected amount, currency, recipient, recent payment date, unique reference code when available, unused transaction ID or unused receipt evidence, and no obvious fraud/tampering signals.
- Approval levels: level 0 uploaded/plausible receipt grants short temporary access; level 1 strong receipt match with amount/currency/recipient/recent date/reference or transaction ID grants longer temporary access; level 2 PayPal-side/admin confirmation grants confirmed paid access; level 3 full PayPal Checkout/webhook/capture integration grants automatic confirmed access.
- Admin dashboard should show user account, expected amount/currency, reference code, uploaded receipt, AI-extracted payment details, confidence/risk assessment, access status, temporary expiry, PayPal transaction ID, decision history, and internal notes.
- Admin actions should include confirm payment, reject payment, extend temporary access, revoke access, mark for manual review, and add internal note.
- Reconciliation sources can include PayPal email notifications, PayPal dashboard review, PayPal transaction search/API when available, admin review, and future PayPal Checkout/webhook integration.
- Fraud controls: unique reference per attempt, no reused transaction IDs, no reused receipt evidence across accounts, automatic temporary expiry, stronger confirmation for permanent access, manual review for suspicious uploads, logged AI decisions, logged admin decisions, rate/attempt limits for temporary access, and automatic blocking for mismatched amount/currency/recipient or stale payment date.
- Risk flags include missing transaction ID, missing/wrong recipient, wrong amount, wrong currency, old payment date, reused transaction ID, visible screenshot edits, cropped/incomplete receipt, payer identity mismatch, missing reference code, and multiple failed attempts by one user.
- User messaging should be explicit: uploaded proof may grant temporary access while payment is verified; temporary approval can expire; confirmed payment activates access normally; rejection should tell the user to check amount, currency, recipient, and reference code.
- Current MVP scope implemented without PayPal API/webhooks: pending attempt creation, PayPal payment instructions, reference code generation, receipt upload, local text/PDF plausibility review, temporary credit grants, admin review dashboard, manual confirm/reject/manual-review actions, and audit logging. Still add automatic expiry enforcement/revocation and image/OCR or model-assisted receipt extraction later if needed.
- Out of MVP scope: full Stripe integration, full PayPal Checkout integration, subscriptions, refunds, tax handling, invoice generation, chargeback/dispute workflows, and fully automated permanent confirmation.

Acceptance criteria:

- This repo can run and pass tests with the submodule initialized, while still using the default provider when private production admin/payment wiring is not configured.
- With the private submodule installed/configured, shared contracts/helpers/components are used through host-owned adapter and API boundaries.
- Public app code does not import private implementation details outside the host adapter/UI boundary.
- `ai-portfolio-admin` imports no host app modules; dependency direction remains host app -> shared submodule.
- Manual credit grants and usage debits still work in the default implementation.
- Payment-provider events are idempotent, auditable, and can grant credits without duplicating balances.
- Free-credit requests and PayPal receipt reviews are persisted, reviewable by admins, and can result in audited credit grants.

### 9. Usage Credits And Provider-Ready Billing

Status: first backend pass implemented; shared-admin foundation complete; PayPal receipt funding implemented; free-credit request flow and complete usage coverage remain planned.

Reference `../plodai` directly during implementation and align models/services/schemas with it where the concepts match, adapting names only where this app's library/task surfaces need richer references.

PlodAI reference setup:

- Clerk sign-up creates the account, but app access is still gated by manual activation.
- Clerk metadata carries `active`, `role`, and `credit_floor_usd`; activation sets a default negative floor so a newly activated user can try the product before payments exist.
- The database stores `user_credit_balances`, `credit_grants`, and `cost_events`.
- Admin endpoints and an admin UI list Clerk users, show activation/balance state, activate/deactivate users, and grant manual USD credit with an audit note.
- ChatKit usage is converted to a platform cost by applying a multiplier to OpenAI token/transcription pricing, accumulated into thread metadata, recorded as cost events, and deducted from the user's credit balance.
- Non-admin users are blocked when their current credit reaches the configured floor; admins bypass the paid-user gate.
- Stripe is not implemented there yet; the manual grant ledger is the bridge that can later receive payment-created credits from Stripe, PayPal, or another provider.

Decision:

- Bring the same account monetization model here: manual activation first, free trial credit on activation, prepaid USD credit balances, cost-event debits, and a configurable markup over underlying OpenAI API cost.
- Start with a default markup in the `1.2x` to `1.5x` range, exposed as settings rather than hardcoded, so the business rule can change without a migration.
- Treat external payment providers as future credit-funding sources, not as the first implementation dependency. The first pass should make the ledger, admin grant flow, and usage debiting correct even when all credits are granted manually.
- Cover every expensive OpenAI-backed path, not only ChatKit text turns: ChatKit agent runs, transcription/voice if enabled, image generation, research discovery/answers, source-file vector uploads/searches where measurable, and any future generation actions.

Implementation notes:

- Completed first pass: added `user_credit_balances`, `credit_grants`, and `cost_events` tables with migration coverage; added `BillingService` for grants, idempotent debits, cost calculations, and credit-floor checks; exposed `/api/billing/me`, `/api/admin/users`, `/api/admin/users/set-active`, and `/api/admin/credits/grant`; added current credit and floor fields to `/api/auth/me`; gated billable REST operations before long-running work; recorded ChatKit response usage as auditable cost events when response usage is available; and records QA/freeform action usage from REST, MCP, or ChatKit tool calls.
- Use these PlodAI files as the first implementation reference: `../plodai/backend/app/models/credit.py`, `../plodai/backend/app/models/credit_grant.py`, `../plodai/backend/app/models/cost.py`, `../plodai/backend/app/services/credit_service.py`, `../plodai/backend/app/core/clerk_metadata.py`, `../plodai/backend/app/core/auth.py`, `../plodai/backend/app/services/clerk_admin_service.py`, `../plodai/backend/app/schemas/credits.py`, `../plodai/backend/app/api/routes.py`, `../plodai/backend/app/chatkit/usage.py`, `../plodai/backend/app/chatkit/metadata.py`, `../plodai/frontend/src/components/AdminCreditsPanel.tsx`, and `../plodai/frontend/src/types/credits.ts`.
- Reuse this repo's existing Clerk and `AppUser` foundation: `private_metadata` already provides `active` and `role`, and local `AppUser` records already mirror Clerk identity.
- Align table shape and behavior with PlodAI's `UserCreditBalance`, `CreditGrant`, and `CostEvent` where possible so credit grants, balance debits, cost audit records, admin summaries, and future provider-created grants stay portable between the two projects.
- Add billing metadata support for a per-user credit floor, activation defaults, and admin-only active/role/balance management without weakening the local-dev auth path.
- Add app-owned billing tables with migrations and drift tests: current balance, manual/admin credit grants, cost events with user/thread/task/source/action/openai response IDs when available, pricing version, raw usage summary, platform multiplier, and optional provider/payment reference fields for later.
- Add a `BillingService` or equivalent boundary that can grant credits, compute marked-up cost, record idempotent debits, enforce balance floors, and expose concise balance/status responses to web, ChatKit, MCP, and tests.
- Centralize OpenAI pricing data and model it as configuration/versioned constants. Unknown models should log a warning and choose a deliberate policy: block, charge zero with an audit event, or require an explicit fallback price.
- Gate billable user operations before starting long work where possible, then record actual cost after completion using the logged response IDs and usage data already captured by the OpenAI/ChatKit observability layer.
- Add admin REST endpoints and a compact admin view for user search, activation/deactivation, manual credit grants with notes, balance display, recent grants/costs, and low/empty credit status.
- Keep user-facing billing light in the normal workspace: show current credit/remaining trial state and clear blocked-state copy, without adding checkout flows until the ledger and shared-admin boundary are proven.
- Prepare provider integrations by reserving fields for payment provider, checkout/session/order/payment intent IDs, payment status, and credit amount, then later add webhook/callback-driven credit grants with idempotency.
- Completed PayPal receipt funding pass: added payment-attempt persistence, receipt upload/review, temporary PayPal credit grants, account-panel payment instructions, and admin payment-attempt review.
- Next pass: record post-completion cost events for the remaining non-ChatKit OpenAI paths such as semantic split, research discovery, source vector indexing/search, image, voice, and transcription; add free-credit requests; and add checkout/webhook-created credit grants once the manual and receipt ledgers have been exercised.

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
- Removed the selected-file retrieval-scope path from normal ChatKit usage in favor of explicit `@` file references, source IDs, and reveal/search tools.
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
- ChatKit threads persist OpenAI conversation state without carrying browser file-selection scope.
- ChatKit agent runs use OpenAI conversation IDs for durable context and track response IDs for logs/debugging.
- Client-tool calls coordinate file reveal, search changes, entity references, and research builder state.
- ChatKit treats explicit `@` file references and tool-provided source IDs as retrieval scope; direct file inputs are capped to a small number of small files and are only attached on user-message turns.
- ChatKit Responses requests enable server-side context compaction with a configurable compact threshold.
- ChatKit can name threads early through a side-effect tool that updates thread title metadata.
- ChatKit tool responses are compacted to avoid passing OpenAI file IDs, full tag objects, raw metadata, and bulky result payloads back through conversation state.
- Tag slugs are treated as stable tag identifiers for ChatKit; backend tag-filter paths accept tag slugs as well as legacy tag IDs.

Final MCP verification follow-ups:

- Deployed MCP must be usable from the user's ChatGPT account against the beta service URL, not only through local clients.
- FastMCP dev server should be the local verification path for MCP tool discovery, MCP Apps UI rendering, resource metadata, and end-to-end tool calls before deployment.
- Add a small dev-only MCP entrypoint, such as `backend/app/mcp/dev_server.py`, that exports `mcp = create_dev_mcp_server(get_settings(), create_services(settings))` so FastMCP dev tooling can import the current unauthenticated dev server directly.
- FastMCP Apps UI command should be documented as `./.venv/bin/fastmcp dev apps backend/app/mcp/dev_server.py:mcp --mcp-port 8001 --dev-port 8080 --no-reload`, using port `8001` to avoid colliding with the normal FastAPI app on `8000`.
- FastMCP Inspector command should be documented as `./.venv/bin/fastmcp dev inspector backend/app/mcp/dev_server.py:mcp --ui-port 6274 --server-port 6277 --no-reload`.
- Production/deployed HTTP MCP remains the FastAPI-mounted server at `/mcp/`; local FastMCP dev tooling should use `create_dev_mcp_server` because `create_mcp_server` includes the production token verifier.
- MCP Apps UI should expose screenshot-worthy flows for research-library building/status, semantic search, tag filtering, source detail/preview, and recent task state.
- MCP tools/resources should expose research actions and semantic/tag search clearly enough for ChatGPT hosts to use them without relying on the web frontend.
- After an MCP file search, ChatGPT should be able to request and receive the raw file content or appropriate extracted text for selected results, subject to size/safety limits and without leaking unrelated files.
- Final screenshots should include the MCP Apps UI in ChatGPT or FastMCP dev tooling, plus at least one successful search-to-content retrieval flow.
- Add or update tests/docs for MCP Apps resources, deployed MCP auth/config, raw content retrieval, and FastMCP dev-server usage.

### Logging And Debugging

Status: complete for the current baseline.

- Backend OpenAI calls log response IDs, conversation IDs, request IDs, model/status, token totals, duration, and clickable platform log URLs.
- FastAPI request logs and framework logs reach the configured file log.
- `skills/openai-log-debugger` can fetch logged Responses and Conversations artifacts into `.local/openai-debug/`.
- Logs avoid prompts, secrets, and bulky response bodies.

### Browser UX Fixes

Status: mostly complete; view split first pass implemented.

- Hidden empty preview.
- Wider source preview.
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
