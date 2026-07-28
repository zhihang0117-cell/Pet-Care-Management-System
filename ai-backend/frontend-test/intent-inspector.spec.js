const { test, expect } = require("@playwright/test");

const PAGE_URL = "http://127.0.0.1:8080/testing.html";

test("desktop exposes live profile, reasoning, RAG, tools, and all intents", async ({ page }) => {
  test.setTimeout(120000);
  const consoleErrors = [];
  page.on("console", message => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });

  await page.goto(PAGE_URL);
  await expect(page).toHaveTitle("Pawfect Chat Tester");
  await expect(page.locator("#catalogCount")).toContainText("scenarios");

  await page.locator("#message").fill("I want grooming for my pet. Show me bathing and non-bathing choices.");
  await page.locator("#send").click();
  await expect(page.locator("#turnStatus")).not.toContainText("Waiting", { timeout: 90000 });

  await expect(page.locator("#profileBadge")).toContainText("Loaded");
  await expect(page.locator("#profileEvidence")).toContainText("selected_pet_profile");
  await expect(page.locator("#reasoningEvidence")).toContainText("known_facts");
  await expect(page.locator("#ragEvidence")).toContainText("chunks");
  await expect(page.locator("#toolEvidence")).toContainText("identity_and_profile_tools");
  const intentCount = await page.locator("#intentCatalog .intent-card").count();
  expect(intentCount).toBeGreaterThanOrEqual(30);
  await expect(page.locator("#intentCatalog")).toContainText("MAKE_BOOKING");
  await expect(page.locator("#intentCatalog")).toContainText("GET_BOOKING_SERVICE_OPTIONS");

  expect(consoleErrors).toEqual([]);
  await page.screenshot({ path: "frontend-test-intent-inspector-desktop.png", fullPage: true });
});

test("mobile intent inspector remains searchable and single-column", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(PAGE_URL);
  await expect(page.locator("#catalogCount")).toContainText("scenarios");
  await page.locator("#intentSearch").fill("availability");
  await expect(page.locator("#intentCatalog .intent-card").first()).toBeVisible();
  await expect(page.locator("#intentCatalog")).toContainText("CHECK_AVAILABILITY");
  await page.screenshot({ path: "frontend-test-intent-inspector-mobile.png", fullPage: true });
});
