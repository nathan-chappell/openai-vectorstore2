import { defineConfig, devices } from "@playwright/test";

const backendEnv: Record<string, string> = {
  ...process.env,
  OPENAI_API_KEY: process.env.OPENAI_API_KEY || "sk-playwright",
  APP_SIGNING_SECRET: process.env.APP_SIGNING_SECRET || "playwright-secret",
  ALLOW_LOCAL_DEV_AUTH: "true",
  DATABASE_URL: "sqlite+aiosqlite:///./.local/playwright/app.db",
  LOCAL_STORAGE_DIR: ".local/playwright/storage",
  STATIC_DIR: "frontend/dist",
};

export default defineConfig({
  testDir: "frontend/e2e",
  outputDir: "output/playwright",
  fullyParallel: true,
  timeout: 30_000,
  expect: {
    timeout: 10_000,
  },
  webServer: [
    {
      command: "./.venv/bin/uvicorn backend.app.main:create_fastapi_app --factory --host 127.0.0.1 --port 8000",
      url: "http://127.0.0.1:8000/health",
      timeout: 60_000,
      reuseExistingServer: !process.env.CI,
      env: backendEnv,
    },
    {
      command: "VITE_CLERK_PUBLISHABLE_KEY= npm run dev -- --host 127.0.0.1",
      url: "http://127.0.0.1:5173",
      timeout: 60_000,
      reuseExistingServer: !process.env.CI,
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
