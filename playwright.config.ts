import { defineConfig, devices } from "@playwright/test";
import { existsSync } from "node:fs";

if (typeof process.loadEnvFile === "function" && existsSync(".env")) {
  process.loadEnvFile(".env");
}

const clerkDisabledEnv: Record<string, string> = {
  ALLOW_LOCAL_DEV_AUTH: "true",
  CLERK_ISSUER_URL: "",
  CLERK_SECRET_KEY: "",
  VITE_CLERK_PUBLISHABLE_KEY: "",
};

const baseEnv: Record<string, string> = Object.fromEntries(
  Object.entries(process.env).filter((entry): entry is [string, string] => typeof entry[1] === "string"),
);

const requiredEnvNames = [
  "OPENAI_API_KEY",
  "APP_SIGNING_SECRET",
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

const backendEnv: Record<string, string> = {
  ...baseEnv,
  DATABASE_URL: `sqlite+aiosqlite:///./.local/playwright/app-${Date.now()}.db`,
  LOCAL_STORAGE_DIR: ".local/playwright/storage",
  STORAGE_BACKEND: "s3",
  STATIC_DIR: "frontend/dist",
  ...clerkDisabledEnv,
};

const frontendEnv: Record<string, string> = {
  ...baseEnv,
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
  webServer: [
    {
      command: "./.venv/bin/uvicorn backend.app.main:create_fastapi_app --factory --host 127.0.0.1 --port 8000",
      url: "http://127.0.0.1:8000/health",
      timeout: 60_000,
      reuseExistingServer,
      env: backendEnv,
    },
    {
      command: "npm run dev -- --host 127.0.0.1",
      url: "http://127.0.0.1:5173",
      timeout: 60_000,
      reuseExistingServer,
      env: frontendEnv,
    },
  ],
  use: {
    baseURL: "http://127.0.0.1:5173",
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
