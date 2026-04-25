import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const BACKEND_URL = "http://127.0.0.1:8000";
const AUTH_HEADERS = { Authorization: "Bearer local-dev" };

type ChatKitAttachment = {
  id: string;
  type: "file";
  name: string;
  mime_type: string;
  metadata?: Record<string, unknown>;
};

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
};

test("workspace shell loads with local-dev auth", async ({ page }, testInfo) => {
  await page.goto("/");

  await expect(page.getByText("Local dev auth")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Semantic Library" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Search And Branch" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "QA, Freeform, Image, Voice" })).toBeVisible();
  await expect(page.getByText("Recent Tasks")).toBeVisible();
  await expect(page.locator(".chat-panel")).toBeVisible();
  await expect(page.getByRole("button", { name: "Refresh" })).toBeEnabled();

  await page.screenshot({ path: testInfo.outputPath("workspace-shell.png"), fullPage: true });
});

test("chatkit uploads a file, answers from it, and deletes it", async ({ page, request }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium-desktop", "Run the live ChatKit flow once.");
  test.setTimeout(300_000);

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

    const attachment = await uploadAttachmentFromBrowser(page, filename, fileText);
    expect(attachment.type).toBe("file");
    expect(attachment.name).toBe(filename);
    expect(attachment.metadata).toBeTruthy();
    sourceId = stringMetadata(attachment, "source_id");
    const taskId = stringMetadata(attachment, "task_id");
    expect(sourceId).toBeTruthy();
    expect(taskId).toBeTruthy();

    await waitForTask(request, taskId, "completed", 120_000);
    const source = await getSource(request, sourceId);
    expect(source.status).toBe("ready");

    await sendChatKitMessage(
      page,
      [
        `Use the attached source ${sourceId} and call answer_from_library.`,
        "Question: what is the project codename and retention policy?",
        "Use only that uploaded file and include the exact phrases Cobalt Maple and seven years.",
      ].join(" "),
      attachment,
    );

    const qaTask = await waitForTaskMatching(
      request,
      (task) => task.kind === "qa" && task.origin_surface === "chatkit" && jsonText(task.result_json).includes("cobalt maple"),
      120_000,
    );
    expect(jsonText(qaTask.result_json)).toContain("seven years");

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

async function uploadAttachmentFromBrowser(page: Page, filename: string, content: string): Promise<ChatKitAttachment> {
  return page.evaluate(
    async ({ filename: uploadName, content: uploadContent }) => {
      const formData = new FormData();
      formData.set("file", new File([uploadContent], uploadName, { type: "text/plain" }), uploadName);
      const response = await fetch("/api/chatkit/attachments", {
        method: "POST",
        headers: { Authorization: "Bearer local-dev" },
        body: formData,
      });
      if (!response.ok) {
        throw new Error(`ChatKit attachment upload failed: ${response.status} ${await response.text()}`);
      }
      return (await response.json()) as ChatKitAttachment;
    },
    { filename, content },
  );
}

async function sendChatKitMessage(page: Page, text: string, attachment?: ChatKitAttachment): Promise<void> {
  await page.evaluate(
    async ({ text: messageText, attachment: uploadedAttachment }) => {
      const chatkit = document.querySelector("openai-chatkit") as unknown as {
        sendUserMessage(params: { text: string; attachments?: ChatKitAttachment[] }): Promise<void>;
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
        attachments: uploadedAttachment ? [uploadedAttachment] : undefined,
      });
      await responseDone;
    },
    { text, attachment },
  );
}

async function waitForTask(
  request: APIRequestContext,
  taskId: string,
  expectedStatus: string,
  timeoutMs: number,
): Promise<TaskSummary> {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    const response = await request.get(`${BACKEND_URL}/api/tasks/${taskId}`, { headers: AUTH_HEADERS });
    if (response.ok()) {
      const task = (await response.json()) as TaskSummary;
      if (task.status === expectedStatus) {
        return task;
      }
      if (task.status === "failed" || task.status === "cancelled") {
        throw new Error(`Task ${taskId} ended as ${task.status}: ${task.error_message ?? "no error"}`);
      }
    }
    await delay(1_000);
  }
  throw new Error(`Timed out waiting for task ${taskId} to become ${expectedStatus}.`);
}

async function waitForTaskMatching(
  request: APIRequestContext,
  matches: (task: TaskSummary) => boolean,
  timeoutMs: number,
): Promise<TaskSummary> {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    const response = await request.get(`${BACKEND_URL}/api/tasks?limit=50`, { headers: AUTH_HEADERS });
    if (response.ok()) {
      const payload = (await response.json()) as TaskListResponse;
      const match = payload.tasks.find(matches);
      if (match) {
        return match;
      }
    }
    await delay(1_000);
  }
  throw new Error("Timed out waiting for a matching task.");
}

async function getSource(request: APIRequestContext, sourceId: string): Promise<SourceDetail> {
  const response = await request.get(`${BACKEND_URL}/api/sources/${sourceId}`, { headers: AUTH_HEADERS });
  expect(response.status()).toBe(200);
  return (await response.json()) as SourceDetail;
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

function stringMetadata(attachment: ChatKitAttachment, key: string): string {
  const value = attachment.metadata?.[key];
  if (typeof value !== "string" || !value.trim()) {
    throw new Error(`Attachment metadata ${key} was missing.`);
  }
  return value;
}

function jsonText(value: unknown): string {
  return JSON.stringify(value ?? {}).toLowerCase();
}

async function delay(ms: number): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, ms));
}
