import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { resolve } from "node:path";

const BACKEND_URL = `http://127.0.0.1:${process.env.PLAYWRIGHT_BACKEND_PORT ?? "8000"}`;
const AUTH_HEADERS = { Authorization: "Bearer local-dev" };
const FIXTURE_NOW = "2026-04-26T19:55:00.000Z";

type TaskSummary = {
  id: string;
  kind: string;
  status: string;
  origin_surface: string;
  origin_thread_id: string | null;
  source_file_id: string | null;
  input_json: unknown;
  result_json: unknown;
  error_message: string | null;
};

type TaskListResponse = {
  tasks: TaskSummary[];
};

type SourceDetail = {
  id: string;
  status: string;
  display_title: string;
  original_filename: string;
};

type SourceListResponse = {
  sources: SourceDetail[];
};

type FixtureRecord = Record<string, unknown>;

function filesystemEntry(overrides: FixtureRecord = {}): FixtureRecord {
  return {
    id: "entry",
    kind: "folder",
    name: "Files",
    path: "/",
    parent_id: null,
    source_id: null,
    source_kind: null,
    media_type: null,
    status: null,
    byte_size: null,
    chunk_count: null,
    description: null,
    summary: null,
    suggested_tags: [],
    tags: [],
    openai_original_file_id: null,
    openai_vector_file_id: null,
    created_at: FIXTURE_NOW,
    updated_at: FIXTURE_NOW,
    ...overrides,
  };
}

function sourceSummary(overrides: FixtureRecord = {}): FixtureRecord {
  return {
    id: "source",
    filesystem_entry_id: null,
    virtual_name: null,
    virtual_path: null,
    display_title: "Source",
    original_filename: "source.txt",
    media_type: "text/plain",
    source_kind: "text",
    status: "ready",
    byte_size: 1024,
    chunk_count: 0,
    description: null,
    summary: null,
    suggested_tags: [],
    error_message: null,
    created_at: FIXTURE_NOW,
    updated_at: FIXTURE_NOW,
    tags: [],
    openai_original_file_id: "file_original_fixture",
    openai_original_file_purpose: "user_data",
    openai_vector_file_id: "vs_file_fixture",
    vector_attributes: null,
    ...overrides,
  };
}

function taskSummary(overrides: FixtureRecord = {}): FixtureRecord {
  return {
    id: "task",
    kind: "research_import",
    status: "completed",
    title: "Task",
    origin_surface: "web",
    origin_thread_id: null,
    source_file_id: null,
    input_json: {},
    result_json: {},
    error_message: null,
    started_at: FIXTURE_NOW,
    completed_at: FIXTURE_NOW,
    created_at: FIXTURE_NOW,
    updated_at: FIXTURE_NOW,
    ...overrides,
  };
}

function researchCandidate(overrides: FixtureRecord = {}): FixtureRecord {
  return {
    id: "candidate",
    task_id: "task",
    status: "pending",
    source_type: "url",
    url: "https://example.com/reference.txt",
    normalized_url: "https://example.com/reference.txt",
    title: "Reference",
    description: "Candidate description.",
    summary: "Candidate summary.",
    suggested_tags: ["research"],
    authors: ["Ada Lovelace"],
    published_at: "2024",
    doi: null,
    arxiv_id: null,
    rationale: "Relevant public source.",
    score: 0.86,
    depth: 1,
    parent_candidate_id: null,
    parent_source_file_id: null,
    linked_source_file_id: null,
    provenance: {},
    content_hash: null,
    error_message: null,
    created_at: FIXTURE_NOW,
    updated_at: FIXTURE_NOW,
    ...overrides,
  };
}

test("workspace shell loads with local-dev auth", async ({ page }, testInfo) => {
  await page.addInitScript(() => {
    window.localStorage.removeItem("openai-vectorstore2.workspaceSplitPercent");
  });
  await page.goto("/");

  await expect(page.getByText("Local dev auth")).toBeVisible();
  if (testInfo.project.name === "chromium-desktop") {
    await expect(page.getByText("Recent Tasks")).toBeVisible();
  }
  await expect(page.locator(".explorer-pane")).toBeVisible();
  await expect(page.locator(".explorer-detail")).toHaveCount(0);
  await expect(page.locator(".filesystem-layout")).not.toHaveClass(/has-preview/);
  await expect(page.locator(".chat-panel")).toBeVisible();
  await expect(page.locator("openai-chatkit")).toBeVisible();
  await expect(page.getByPlaceholder("Find in this folder")).toBeVisible();
  await expect(page.getByRole("tab", { name: "Explorer" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tab", { name: "Library" })).toBeVisible();
  await expect(page.getByText("0 indexed files selected")).toBeVisible();
  await expect(page.locator(".explorer-commandbar").getByRole("button", { name: "New Folder" })).toBeEnabled();
  await expect(page.locator(".explorer-commandbar").getByRole("button", { name: "Up" })).toHaveCount(0);
  await expect(page.locator(".explorer-commandbar").getByRole("button", { name: "Rename" })).toHaveCount(0);
  await expect(page.locator(".explorer-commandbar").getByRole("button", { name: "Delete" })).toHaveCount(0);
  await expect(page.getByText("Add files")).toBeVisible();
  await expect(page.locator(".research-builder-strip")).toBeVisible();
  await expect(page.getByPlaceholder("Topic or paper title")).toBeVisible();
  await expect(page.getByRole("button", { name: "Build" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Refresh" })).toBeEnabled();
  if (testInfo.project.name === "chromium-desktop") {
    await dragWorkspaceSplitter(page, 52);
    await expect(page.locator(".workspace-splitter")).toHaveAttribute("aria-valuenow", "52");
    await expect
      .poll(() => page.evaluate(() => window.localStorage.getItem("openai-vectorstore2.workspaceSplitPercent")))
      .toBe("52");
  } else {
    await expect(page.locator(".workspace-splitter")).toBeHidden();
  }

  await page.screenshot({ path: testInfo.outputPath("workspace-shell.png"), fullPage: true });
});

test("explorer local search and library semantic append use separate views", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium-desktop", "Dense search view coverage is desktop-focused.");

  const rootEntry = filesystemEntry({ id: "root", kind: "folder", name: "Files", path: "/" });
  const alphaEntry = filesystemEntry({
    id: "entry-alpha",
    kind: "file",
    name: "alpha-notes.txt",
    path: "/alpha-notes.txt",
    parent_id: rootEntry.id,
    source_id: "source-alpha",
    source_kind: "text",
    media_type: "text/plain",
    status: "ready",
    summary: "Notes about retrieval quality.",
    tags: [{ id: "tag-rag", name: "RAG", slug: "rag", color: null, source: "manual", source_count: 1 }],
  });
  const bravoEntry = filesystemEntry({
    id: "entry-bravo",
    kind: "file",
    name: "bravo-plan.txt",
    path: "/bravo-plan.txt",
    parent_id: rootEntry.id,
    source_id: "source-bravo",
    source_kind: "text",
    media_type: "text/plain",
    status: "ready",
    summary: "Roadmap for evals.",
  });
  const alphaSource = sourceSummary({
    id: "source-alpha",
    filesystem_entry_id: "entry-alpha",
    virtual_name: "alpha-notes.txt",
    virtual_path: "/alpha-notes.txt",
    display_title: "Alpha Notes",
    original_filename: "alpha-notes.txt",
    summary: "Notes about retrieval quality.",
    tags: [{ id: "tag-rag", name: "RAG", slug: "rag", color: null, source: "manual", source_count: 1 }],
  });
  const bravoSource = sourceSummary({
    id: "source-bravo",
    filesystem_entry_id: "entry-bravo",
    virtual_name: "bravo-plan.txt",
    virtual_path: "/bravo-plan.txt",
    display_title: "Bravo Plan",
    original_filename: "bravo-plan.txt",
    summary: "Roadmap for evals.",
  });
  const searchQueries: string[] = [];

  await page.route("**/api/tags", async (route) => {
    await route.fulfill({
      json: [{ id: "tag-rag", name: "RAG", slug: "rag", color: null, source: "manual", source_count: 1 }],
    });
  });
  await page.route("**/api/filesystem**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/filesystem/search")) {
      await route.fulfill({ json: { query: url.searchParams.get("query"), entries: [alphaEntry], total_count: 1, page: 1, page_size: 1, has_more: false } });
      return;
    }
    await route.fulfill({
      json: {
        current: rootEntry,
        breadcrumbs: [{ id: rootEntry.id, name: "Files", path: "/" }],
        entries: [alphaEntry, bravoEntry],
      },
    });
  });
  await page.route("**/api/sources/source-alpha", async (route) => {
    await route.fulfill({ json: { ...alphaSource, storage_provider: "local", storage_key: "alpha", ingest_strategy: "source", metadata: {}, chunks: [] } });
  });
  await page.route("**/api/search", async (route) => {
    const payload = route.request().postDataJSON() as { query: string; tag_ids: string[] };
    searchQueries.push(payload.query);
    await route.fulfill({
      json: {
        query: payload.query,
        hits: [
          {
            chunk_id: `source:${payload.query.includes("bravo") ? "source-charlie" : "source-alpha"}`,
            source_file_id: payload.query.includes("bravo") ? "source-charlie" : "source-alpha",
            source_title: payload.query.includes("bravo") ? "Charlie Paper" : "Alpha Notes",
            original_filename: payload.query.includes("bravo") ? "charlie-paper.pdf" : "alpha-notes.txt",
            score: 0.91,
            title: "Match",
            summary: "Matched semantic text.",
            text: "Matched semantic text.",
            tags: payload.tag_ids,
            locator: { type: "generated", start_page: null, end_page: null, start_line: null, end_line: null, start_seconds: null, end_seconds: null },
            openai_file_id: null,
            attributes: payload.query.includes("bravo")
              ? { virtual_name: "charlie-paper.pdf", virtual_path: "/Archives/charlie-paper.pdf" }
              : null,
          },
        ],
      },
    });
  });

  await page.goto("/");
  await page.getByPlaceholder("Find in this folder").fill("retrieval");
  await expect(page.locator(".file-rows")).toContainText("alpha-notes.txt");
  await expect(page.locator(".file-rows")).not.toContainText("bravo-plan.txt");

  await page.getByRole("tab", { name: "Library" }).click();
  await page.getByRole("button", { name: "RAG" }).click();
  await page.getByPlaceholder("indexed files").fill("");
  await page.keyboard.press("Enter");
  await expect.poll(() => searchQueries).toEqual(["indexed files"]);
  await expect(page.locator(".library-result-list")).toContainText("alpha-notes.txt");

  await page.getByPlaceholder("indexed files").fill("bravo");
  await page.keyboard.press("Control+Enter");
  await expect.poll(() => searchQueries).toEqual(["indexed files", "bravo"]);
  await expect(page.locator(".library-result-list")).toContainText("alpha-notes.txt");
  await expect(page.locator(".library-result-list")).toContainText("charlie-paper.pdf");
  await expect(page.locator(".library-result-list")).toContainText("/Archives/charlie-paper.pdf");
  await page.getByRole("button", { name: "Select results" }).click();
  await expect(page.getByRole("checkbox", { name: "Select alpha-notes.txt for chat" })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: "Select charlie-paper.pdf for chat" })).toBeChecked();
  await page.screenshot({ path: testInfo.outputPath("workspace-library-search.png"), fullPage: true });
});

test("file explorer shortcuts rename, navigate up, and delete", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium-desktop", "Keyboard shortcut coverage is desktop-focused.");

  let fileName = "shortcut-note.txt";
  let deleted = false;
  const rootEntry = filesystemEntry({
    id: "root",
    kind: "folder",
    name: "Files",
    path: "/",
  });
  const shortcutFolder = filesystemEntry({
    id: "folder-shortcuts",
    kind: "folder",
    name: "Shortcuts",
    path: "/Shortcuts",
    parent_id: "root",
  });
  const shortcutFile = (): FixtureRecord =>
    filesystemEntry({
      id: "entry-shortcut-note",
      kind: "file",
      name: fileName,
      path: `/Shortcuts/${fileName}`,
      parent_id: shortcutFolder.id,
      source_id: null,
      source_kind: "text",
      media_type: "text/plain",
      status: "ready",
      byte_size: 128,
    });
  const secondShortcutFile = (): FixtureRecord =>
    filesystemEntry({
      id: "entry-second-shortcut-note",
      kind: "file",
      name: "second-shortcut-note.txt",
      path: "/Shortcuts/second-shortcut-note.txt",
      parent_id: shortcutFolder.id,
      source_id: null,
      source_kind: "text",
      media_type: "text/plain",
      status: "ready",
      byte_size: 256,
    });

  await page.route("**/api/filesystem/entries/**", async (route) => {
    expect(route.request().method()).toBe("PATCH");
    const payload = route.request().postDataJSON() as { name?: string };
    expect(payload.name).toBe("renamed-shortcut-note.txt");
    fileName = payload.name;
    await route.fulfill({ json: shortcutFile() });
  });
  await page.route("**/api/filesystem/delete", async (route) => {
    expect(route.request().method()).toBe("POST");
    const payload = route.request().postDataJSON() as { entry_ids: string[]; confirm: boolean };
    expect(payload).toEqual({ entry_ids: ["entry-shortcut-note", "entry-second-shortcut-note"], confirm: true });
    await new Promise((resolve) => setTimeout(resolve, 150));
    deleted = true;
    await route.fulfill({ json: { deleted_entry_ids: ["entry-shortcut-note", "entry-second-shortcut-note"], deleted_source_ids: [] } });
  });
  await page.route("**/api/filesystem**", async (route) => {
    const url = new URL(route.request().url());
    if (
      url.pathname.includes("/filesystem/entries/") ||
      url.pathname.endsWith("/filesystem/delete") ||
      url.pathname.endsWith("/filesystem/search")
    ) {
      await route.fallback();
      return;
    }
    const folderId = url.searchParams.get("folder_id");
    if (folderId === shortcutFolder.id) {
      await route.fulfill({
        json: {
          current: shortcutFolder,
          breadcrumbs: [
            { id: rootEntry.id, name: "Files", path: "/" },
            { id: shortcutFolder.id, name: shortcutFolder.name, path: shortcutFolder.path },
          ],
          entries: deleted ? [] : [shortcutFile(), secondShortcutFile()],
        },
      });
      return;
    }
    await route.fulfill({
      json: {
        current: rootEntry,
        breadcrumbs: [{ id: rootEntry.id, name: "Files", path: "/" }],
        entries: [shortcutFolder],
      },
    });
  });

  await page.goto("/");
  const shortcutFolderRow = page.locator('[data-entry-id="folder-shortcuts"]');
  const shortcutFileRow = page.locator('[data-entry-id="entry-shortcut-note"]');
  await shortcutFolderRow.dblclick();
  await expect(page.locator(".breadcrumb-row")).toContainText("Shortcuts");

  await shortcutFileRow.click();
  page.once("dialog", async (dialog) => {
    expect(dialog.type()).toBe("prompt");
    await dialog.accept("renamed-shortcut-note.txt");
  });
  await page.keyboard.press("F2");
  await expect(page.locator(".file-rows")).toContainText("renamed-shortcut-note.txt");

  await shortcutFileRow.click();
  await page.keyboard.press("Alt+ArrowLeft");
  await expect(page.locator(".breadcrumb-row")).not.toContainText("Shortcuts");

  await shortcutFolderRow.dblclick();
  await shortcutFileRow.click();
  await page.keyboard.press("Shift+ArrowDown");
  await expect(page.locator(".file-rows [role='row'].selected-file-row")).toHaveCount(2);
  await page.keyboard.press("?");
  await expect(page.getByRole("dialog", { name: "Keyboard Shortcuts" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "Keyboard Shortcuts" })).toHaveCount(0);
  await shortcutFileRow.click();
  await page.keyboard.press("Shift+ArrowDown");
  await expect(page.locator(".file-rows [role='row'].selected-file-row")).toHaveCount(2);
  await page.keyboard.press("Delete");
  await expect(page.getByRole("dialog", { name: "Delete selected items?" })).toBeVisible();
  await expect(page.getByText("Folders are deleted recursively")).toBeVisible();
  await page.getByRole("button", { name: "Delete" }).click();
  await expect(page.getByText("Deleting 2 selected items...")).toBeVisible();
  await expect(page.getByRole("button", { name: "Deleting" })).toBeDisabled();
  await expect(page.locator(".file-rows")).toContainText("Folder is empty.");
  await page.screenshot({ path: testInfo.outputPath("workspace-shortcuts.png"), fullPage: true });
});

test("research library builder directly indexes a candidate", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium-desktop", "Dense build flow is covered on desktop.");

  let ingested = false;
  const rootEntry = filesystemEntry({
    id: "root",
    kind: "folder",
    name: "Files",
    path: "/",
  });
  const researchParent = filesystemEntry({
    id: "folder-research",
    kind: "folder",
    name: "Research",
    path: "/Research",
    parent_id: "root",
  });
  const targetFolder = filesystemEntry({
    id: "folder-attention",
    kind: "folder",
    name: "Attention Is All You Need",
    path: "/Research/Attention Is All You Need",
    parent_id: "folder-research",
  });
  const ingestedFile = filesystemEntry({
    id: "entry-transformer-pdf",
    kind: "file",
    name: "attention-reference.txt",
    path: "/Research/Attention Is All You Need/attention-reference.txt",
    parent_id: "folder-attention",
    source_id: "source-transformer-reference",
    source_kind: "text",
    media_type: "text/plain",
    status: "ready",
    byte_size: 4096,
    summary: "A public reference about transformer attention.",
    suggested_tags: ["attention", "transformers"],
  });
  const task = taskSummary({
    id: "task-research-build",
    kind: "research_import",
    status: "completed",
    title: "Research import: Attention Is All You Need",
    result_json: { candidate_count: 1, target_folder_id: targetFolder.id },
  });
  const pendingCandidate = researchCandidate({
    id: "candidate-attention-reference",
    task_id: task.id,
    status: "pending",
    title: "Attention reference",
    summary: "A candidate public reference for the Transformer paper.",
  });
  const ingestedCandidate = {
    ...pendingCandidate,
    status: "ingested",
    linked_source_file_id: "source-transformer-reference",
  };
  const source = sourceSummary({
    id: "source-transformer-reference",
    filesystem_entry_id: ingestedFile.id,
    virtual_name: ingestedFile.name,
    virtual_path: ingestedFile.path,
    display_title: "Attention reference",
    original_filename: "attention-reference.txt",
  });
  const ingestTask = taskSummary({
    id: "task-ingest-reference",
    kind: "ingest",
    status: "completed",
    title: "Ingest: attention-reference.txt",
    source_file_id: source.id,
  });

  await page.route("**/api/filesystem**", async (route) => {
    const url = new URL(route.request().url());
    const folderId = url.searchParams.get("folder_id");
    if (folderId === targetFolder.id) {
      await route.fulfill({
        json: {
          current: targetFolder,
          breadcrumbs: [
            { id: rootEntry.id, name: "Files", path: "/" },
            { id: researchParent.id, name: "Research", path: "/Research" },
            { id: targetFolder.id, name: targetFolder.name, path: targetFolder.path },
          ],
          entries: ingested ? [ingestedFile] : [],
        },
      });
      return;
    }
    await route.fulfill({
      json: {
        current: rootEntry,
        breadcrumbs: [{ id: rootEntry.id, name: "Files", path: "/" }],
        entries: [researchParent],
      },
    });
  });
  await page.route("**/api/research/library-builds", async (route) => {
    expect(route.request().method()).toBe("POST");
    const payload = route.request().postDataJSON() as { query: string; seed_type: string; auto_ingest: boolean };
    expect(payload.query).toBe("Attention Is All You Need");
    expect(payload.seed_type).toBe("paper");
    expect(payload.auto_ingest).toBe(true);
    ingested = true;
    await route.fulfill({
      json: {
        task,
        target_folder_id: targetFolder.id,
        seed_source: null,
        candidates: [ingestedCandidate],
        ingested: [{ source, task: ingestTask }],
        duplicate_count: 0,
      },
    });
  });
  await page.route("**/api/tasks**", async (route) => {
    await route.fulfill({ json: { tasks: ingested ? [ingestTask, task] : [task] } });
  });

  await page.goto("/");
  await expect(page.getByText("Local dev auth")).toBeVisible();
  await page.getByPlaceholder("Topic or paper title").fill("Attention Is All You Need");
  await page.locator(".research-builder-controls select").selectOption("paper");
  await page.locator(".research-builder-controls input[type='number']").first().fill("1");
  await page.locator(".research-builder-controls input[type='number']").nth(1).fill("1");
  await page.locator(".research-builder-strip").getByRole("button", { name: "Build" }).click();

  await expect(page.locator(".breadcrumb-row")).toContainText("Attention Is All You Need");
  await expect(page.locator(".research-candidate-list")).toContainText("Attention reference");
  await expect(page.locator(".research-candidate-list")).toContainText("indexed");
  await expect(page.locator(".research-candidate-row").getByRole("button", { name: "Approve" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Ingest approved" })).toHaveCount(0);
  await expect(page.locator(".research-progress-track")).toBeVisible();
  await expect(page.locator(".research-result-summary")).toContainText("1 indexed");
  await expect(page.locator(".file-rows")).toContainText("attention-reference.txt");
  await page.screenshot({ path: testInfo.outputPath("research-builder-direct-flow.png"), fullPage: true });
});

test("explorer-selected file answers through chatkit and deletes cleanly", async ({ page, request }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium-desktop", "Run the live ChatKit flow once.");
  test.setTimeout(420_000);

  const samplePaths = [resolve("sample_sources/rag-field-notes.txt"), resolve("sample_sources/research-index.json")];
  const sampleFilenames = ["rag-field-notes.txt", "research-index.json"];

  const sourceIds: string[] = [];
  try {
    await page.goto("/");
    await expect(page.getByText("Local dev auth")).toBeVisible();
    await waitForChatKit(page);
    await page.locator(".filesystem-upload textarea").fill("Preserve the named fields and short notes as retrievable facts.");

    await page.locator(".filesystem-upload input[type='file']").setInputFiles(samplePaths);
    await expect(page.getByText("2 staged")).toBeVisible();
    await page.locator(".filesystem-upload").getByRole("button", { name: "Index" }).click();

    const queuedSources = await Promise.all(
      sampleFilenames.map((filename) => waitForSourceRecordByFilename(request, filename, 60_000)),
    );
    sourceIds.push(...queuedSources.map((source) => source.id));
    const readySources = await Promise.all(sourceIds.map((id) => waitForSourceReady(request, id, 240_000)));
    for (const source of readySources) {
      expect(source.status).toBe("ready");
    }
    await page.getByRole("button", { name: "Refresh" }).click();
    await page.locator(".file-rows [role='row']").filter({ hasText: readySources[0].original_filename }).click();
    await expect(page.locator(".explorer-detail")).toBeVisible();
    await expect(page.locator(".filesystem-layout")).toHaveClass(/has-preview/);
    await expect(page.locator(".explorer-detail")).toContainText("Cobalt Maple");
    await page.locator(".file-rows [role='row']").filter({ hasText: readySources[1].original_filename }).click({
      modifiers: ["Control"],
    });
    await expect(page.getByText("2 indexed files selected")).toBeVisible();
    await expect(page.locator(".explorer-selection-summary")).toContainText(readySources[0].original_filename);
    await page.screenshot({ path: testInfo.outputPath("workspace-sample-library.png"), fullPage: true });

    await sendChatKitMessage(
      page,
      [
        "Use the currently selected file scope and call answer_from_library.",
        "Question: what is the project codename and retention policy?",
        "Use only the selected files and include the exact phrases Cobalt Maple and seven years.",
      ].join(" "),
    );

    const qaTask = await waitForTaskMatching(
      request,
      (task) => task.kind === "qa" && task.origin_surface === "chatkit" && jsonText(task.result_json).includes("cobalt maple"),
      120_000,
      (task) =>
        task.kind === "qa" &&
        task.origin_surface === "chatkit" &&
        ["failed", "cancelled"].includes(task.status) &&
        jsonText(task.input_json).toLowerCase().includes("cobalt maple"),
    );
    expect(jsonText(qaTask.result_json)).toContain("seven years");
    for (const sourceId of sourceIds) {
      expect(jsonText(qaTask.input_json)).toContain(sourceId);
    }

    for (const sourceId of [...sourceIds]) {
      await sendChatKitMessage(page, `Delete source ${sourceId}. I explicitly confirm deletion of this uploaded source.`);
      await waitForSourceDeleted(request, sourceId, 120_000);
      sourceIds.splice(sourceIds.indexOf(sourceId), 1);
    }
  } finally {
    for (const sourceId of sourceIds) {
      await request.delete(`${BACKEND_URL}/api/sources/${sourceId}`, {
        headers: AUTH_HEADERS,
        failOnStatusCode: false,
      });
    }
  }
});

async function waitForChatKit(page: Page): Promise<void> {
  await expect(page.locator("openai-chatkit")).toBeVisible();
  await page.waitForFunction(() => {
    const chatkit = document.querySelector("openai-chatkit") as unknown as {
      sendUserMessage?: unknown;
      setThreadId?: unknown;
    } | null;
    return typeof chatkit?.sendUserMessage === "function" && typeof chatkit?.setThreadId === "function";
  });
  await page.evaluate(async () => {
    const chatkit = document.querySelector("openai-chatkit");
    if (!chatkit) {
      throw new Error("ChatKit element was not found.");
    }
    await Promise.race([
      new Promise<void>((resolve, reject) => {
        let onReady: EventListener;
        let onError: EventListener;
        const cleanup = (): void => {
          chatkit.removeEventListener("chatkit.ready", onReady);
          chatkit.removeEventListener("chatkit.error", onError);
        };
        onReady = (): void => {
          cleanup();
          resolve();
        };
        onError = (event: Event): void => {
          cleanup();
          const detail = (event as CustomEvent<{ error?: Error }>).detail;
          reject(new Error(detail?.error?.message ?? "ChatKit emitted an error before ready."));
        };
        chatkit.addEventListener("chatkit.ready", onReady, { once: true });
        chatkit.addEventListener("chatkit.error", onError, { once: true });
      }),
      new Promise<void>((resolve) => window.setTimeout(resolve, 5_000)),
    ]);
  });
}

async function sendChatKitMessage(page: Page, text: string): Promise<void> {
  await page.evaluate(
    async ({ text: messageText }) => {
      const chatkit = document.querySelector("openai-chatkit") as unknown as {
        sendUserMessage(params: { text: string }): Promise<void>;
        addEventListener(type: string, listener: EventListener, options?: AddEventListenerOptions): void;
        removeEventListener(type: string, listener: EventListener): void;
      } | null;
      if (!chatkit) {
        throw new Error("ChatKit element was not found.");
      }
      const responseDone = new Promise<void>((resolve, reject) => {
        let onEnd: EventListener;
        let onError: EventListener;
        const timeout = window.setTimeout(() => {
          cleanup();
          reject(new Error("Timed out waiting for ChatKit response."));
        }, 120_000);
        const cleanup = (): void => {
          window.clearTimeout(timeout);
          chatkit.removeEventListener("chatkit.response.end", onEnd);
          chatkit.removeEventListener("chatkit.error", onError);
        };
        onEnd = (): void => {
          cleanup();
          resolve();
        };
        onError = (event: Event): void => {
          cleanup();
          const detail = (event as CustomEvent<{ error?: Error }>).detail;
          reject(new Error(detail?.error?.message ?? "ChatKit emitted an error."));
        };
        chatkit.addEventListener("chatkit.response.end", onEnd, { once: true });
        chatkit.addEventListener("chatkit.error", onError, { once: true });
      });
      await chatkit.sendUserMessage({
        text: messageText,
      });
      await responseDone;
    },
    { text },
  );
}

async function dragWorkspaceSplitter(page: Page, explorerPercent: number): Promise<void> {
  const grid = page.locator(".workspace-grid");
  const splitter = page.locator(".workspace-splitter");
  const gridBox = await grid.boundingBox();
  const splitterBox = await splitter.boundingBox();
  if (!gridBox || !splitterBox) {
    throw new Error("Workspace splitter layout was not measurable.");
  }
  const targetX = gridBox.x + gridBox.width * (explorerPercent / 100);
  const targetY = splitterBox.y + splitterBox.height / 2;
  await page.mouse.move(splitterBox.x + splitterBox.width / 2, targetY);
  await page.mouse.down();
  await page.mouse.move(targetX, targetY, { steps: 10 });
  await page.mouse.up();
}

async function waitForTaskMatching(
  request: APIRequestContext,
  matches: (task: TaskSummary) => boolean,
  timeoutMs: number,
  fails?: (task: TaskSummary) => boolean,
): Promise<TaskSummary> {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    const response = await request.get(`${BACKEND_URL}/api/tasks?limit=50`, { headers: AUTH_HEADERS });
    if (response.ok()) {
      const payload = (await response.json()) as TaskListResponse;
      const failure = fails ? payload.tasks.find(fails) : undefined;
      if (failure) {
        throw new Error(
          `Task ${failure.id} ended ${failure.status}: ${failure.error_message ?? jsonText(failure.result_json)}`,
        );
      }
      const match = payload.tasks.find(matches);
      if (match) {
        return match;
      }
    }
    await delay(1_000);
  }
  throw new Error("Timed out waiting for a matching task.");
}

async function waitForSourceRecordByFilename(
  request: APIRequestContext,
  filename: string,
  timeoutMs: number,
): Promise<SourceDetail> {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    const response = await request.get(`${BACKEND_URL}/api/sources?query=${encodeURIComponent(filename)}&page_size=50`, {
      headers: AUTH_HEADERS,
    });
    if (response.ok()) {
      const payload = (await response.json()) as SourceListResponse;
      const source = payload.sources.find((candidate) => candidate.original_filename === filename);
      if (source) {
        return source;
      }
    }
    await delay(1_000);
  }
  throw new Error(`Timed out waiting for ${filename} to appear.`);
}

async function waitForSourceReady(
  request: APIRequestContext,
  sourceId: string,
  timeoutMs: number,
): Promise<SourceDetail> {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    const response = await request.get(`${BACKEND_URL}/api/sources/${sourceId}`, { headers: AUTH_HEADERS });
    if (response.ok()) {
      const source = (await response.json()) as SourceDetail;
      if (source.status === "ready") {
        return source;
      }
      if (source.status === "failed") {
        throw new Error(`Source ${sourceId} failed ingestion.`);
      }
    }
    await delay(1_000);
  }
  throw new Error(`Timed out waiting for ${sourceId} to become ready.`);
}

async function waitForSourceDeleted(request: APIRequestContext, sourceId: string, timeoutMs: number): Promise<void> {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    const response = await request.get(`${BACKEND_URL}/api/sources/${sourceId}`, {
      headers: AUTH_HEADERS,
      failOnStatusCode: false,
    });
    if (response.status() === 404) {
      return;
    }
    await delay(1_000);
  }
  throw new Error(`Timed out waiting for source ${sourceId} to be deleted.`);
}

function jsonText(value: unknown): string {
  return JSON.stringify(value ?? {}).toLowerCase();
}

async function delay(ms: number): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, ms));
}
