import { defineConfig, devices } from "@playwright/test";
import { existsSync } from "node:fs";

if (typeof process.loadEnvFile === "function" && existsSync(".env")) {
  process.loadEnvFile(".env");
}

const clerkDisabledEnv: Record<string, string> = {
  ALLOW_LOCAL_DEV_AUTH: "true",
  CLERK_SECRET_KEY: "",
  VITE_CLERK_PUBLISHABLE_KEY: "",
};

const baseEnv: Record<string, string> = Object.fromEntries(
  Object.entries(process.env).filter((entry): entry is [string, string] => typeof entry[1] === "string"),
);

const requiredEnvNames = [
  "OPENAI_API_KEY",
  "S3_ENDPOINT",
  "S3_BUCKET",
  "S3_ACCESS_KEY_ID",
  "S3_SECRET_ACCESS_KEY",
] as const;

for (const name of requiredEnvNames) {
  if (!process.env[name]?.trim()) {
    throw new Error(`Playwright live e2e requires ${name} to be set in the environment or .env.`);
  }
}

const backendPort = process.env.PLAYWRIGHT_BACKEND_PORT ?? "8000";
const backendUrl = `http://127.0.0.1:${backendPort}`;

const backendEnv: Record<string, string> = {
  ...baseEnv,
  DATABASE_URL:
    process.env.PLAYWRIGHT_DATABASE_URL ??
    "sqlite+aiosqlite:///./.local/playwright/app-v20260428.db",
  LOCAL_STORAGE_DIR: ".local/playwright/storage",
  STORAGE_BACKEND: "s3",
  STATIC_DIR: "frontend/dist",
  ...clerkDisabledEnv,
};

const reuseExistingServer = process.env.PLAYWRIGHT_REUSE_EXISTING_SERVER === "true";

export default defineConfig({
  testDir: "frontend/e2e",
  outputDir: "output/playwright",
  fullyParallel: true,
  workers: 1,
  timeout: 60_000,
  expect: {
    timeout: 10_000,
  },
  webServer: {
    command:
      `npm run build && ` +
      `./.venv/bin/uvicorn openai_vectorstore2_backend.app.main:create_fastapi_app ` +
      `--factory --host 127.0.0.1 --port ${backendPort}`,
    url: `${backendUrl}/health`,
    timeout: 60_000,
    reuseExistingServer,
    env: backendEnv,
  },
  use: {
    baseURL: backendUrl,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium-desktop",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1280, height: 900 },
      },
    },
    {
      name: "chromium-mobile",
      use: {
        ...devices["Pixel 5"],
      },
    },
  ],
});
