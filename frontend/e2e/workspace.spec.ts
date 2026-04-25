import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const BACKEND_URL = "http://127.0.0.1:8000";
const AUTH_HEADERS = { Authorization: "Bearer local-dev" };

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

test("workspace shell loads with local-dev auth", async ({ page }, testInfo) => {
  await page.goto("/");

  await expect(page.getByText("Local dev auth")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Files", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Preview", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Search", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Actions", exact: true })).toBeVisible();
  await expect(page.getByText("Recent Tasks")).toBeVisible();
  await expect(page.locator(".explorer-pane")).toBeVisible();
  await expect(page.locator(".preview-pane")).toBeVisible();
  await expect(page.locator(".chat-panel")).toBeVisible();
  await expect(page.getByPlaceholder("Search files, tags, type, status")).toBeVisible();
  await expect(page.getByPlaceholder("Search selected files or the library")).toBeVisible();
  await expect(page.getByText("0 files for chat")).toBeVisible();
  await expect(page.getByRole("button", { name: "Select visible" })).toBeDisabled();
  await expect(page.getByText("Choose files")).toBeVisible();
  await expect(page.getByRole("button", { name: "Refresh" })).toBeEnabled();

  await page.screenshot({ path: testInfo.outputPath("workspace-shell.png"), fullPage: true });
});

test("explorer-selected file answers through chatkit and deletes cleanly", async ({ page, request }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium-desktop", "Run the live ChatKit flow once.");
  test.setTimeout(420_000);

  const marker = `pw-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const filename = `chatkit-live-${marker}.txt`;
  const fileText = [
    `Playwright marker: ${marker}.`,
    "The project codename is Cobalt Maple.",
    "The retention policy is seven years.",
    "The deletion confirmation phrase is clean-room-finish.",
  ].join("\n");

  let sourceId: string | null = null;
  try {
    await page.goto("/");
    await expect(page.getByText("Local dev auth")).toBeVisible();
    await waitForChatKit(page);
    await page.locator(".upload-strip textarea").fill("Keep this small test fixture as one semantic chunk.");

    await page.locator(".upload-strip input[type='file']").setInputFiles({
      name: filename,
      mimeType: "text/plain",
      buffer: Buffer.from(fileText),
    });
    await expect(page.getByText("1 selected")).toBeVisible();
    await page.locator(".upload-strip").getByRole("button", { name: "Upload" }).click();

    const queuedSource = await waitForSourceRecordByFilename(request, filename, 60_000);
    sourceId = queuedSource.id;
    const source = await waitForSourceReady(request, sourceId, 240_000);
    expect(source.status).toBe("ready");
    await page.getByRole("button", { name: "Refresh" }).click();
    await expect(page.getByLabel(`Select ${source.display_title} for chat`)).toBeVisible();
    await page.getByLabel(`Select ${source.display_title} for chat`).check();
    await expect(page.getByText("1 file for chat")).toBeVisible();
    await expect(page.locator(".chat-scope-strip")).toContainText(source.display_title);

    await sendChatKitMessage(
      page,
      [
        "Use the currently selected file scope and call answer_from_library.",
        "Question: what is the project codename and retention policy?",
        "Use only that uploaded file and include the exact phrases Cobalt Maple and seven years.",
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
    expect(jsonText(qaTask.input_json)).toContain(source.id);

    await sendChatKitMessage(
      page,
      `Delete source ${sourceId}. I explicitly confirm deletion of this uploaded source.`,
    );
    await waitForSourceDeleted(request, sourceId, 120_000);
    sourceId = null;
  } finally {
    if (sourceId !== null) {
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
