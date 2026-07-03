const { test, expect } = require("@playwright/test");

test("login page loads without server error", async ({ page }) => {
  const res = await page.goto("/login");
  expect(res.status()).toBeLessThan(500);
});

test("root page loads without server error", async ({ page }) => {
  const res = await page.goto("/");
  expect(res.status()).toBeLessThan(500);
});

test("settings panel tabs switch without closing the panel", async ({ page }) => {
  await page.goto("/");

  // Wait for the main UI to be present
  const settingsBtn = page.locator("#settings-btn");
  await settingsBtn.waitFor({ state: "visible", timeout: 10000 });

  // Open the settings panel
  await settingsBtn.click();
  const panel = page.locator("#settings-panel");
  await expect(panel).not.toHaveClass(/setup-hidden/);

  // Click the first tab (HA settings)
  await page.locator("#ha-settings-btn").click();
  await expect(panel).not.toHaveClass(/setup-hidden/, { message: "panel closed after clicking HA tab" });

  // Switch to a second tab (vision)
  await page.locator("#vision-btn").click();
  await expect(panel).not.toHaveClass(/setup-hidden/, { message: "panel closed when switching to vision tab" });

  // Switch to a third tab (garage)
  await page.locator("#garage-btn").click();
  await expect(panel).not.toHaveClass(/setup-hidden/, { message: "panel closed when switching to garage tab" });
});
