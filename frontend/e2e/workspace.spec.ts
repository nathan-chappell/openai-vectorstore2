import { expect, test } from "@playwright/test";

test("workspace shell loads with local-dev auth", async ({ page }, testInfo) => {
  await page.goto("/");

  await expect(page.getByText("Local dev auth")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Semantic Library" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Search And Branch" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "QA, Freeform, Image, Voice" })).toBeVisible();
  await expect(page.locator(".chat-panel")).toBeVisible();
  await expect(page.getByRole("button", { name: "Refresh" })).toBeEnabled();

  await page.screenshot({ path: testInfo.outputPath("workspace-shell.png"), fullPage: true });
});
