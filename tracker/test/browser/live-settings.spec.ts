import { expect, test } from "@playwright/test";

test("mock draft navigation lives in settings instead of the live header", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript(() => localStorage.setItem("ffb.trackerKey", "test-secret-key"));
  await page.route("**/api/draft", route => route.fulfill({ json: { configured: false, picks: [] } }));
  await page.goto("/");
  await expect(page.locator('.appbar a[href="/mock"]')).toHaveCount(0);
  const link = page.getByRole("link", { name: "Mock draft", exact: true });
  await expect(link).toBeHidden();
  await page.getByRole("button", { name: "Board settings and API key" }).click();
  await expect(page.getByRole("heading", { name: "Board settings" })).toBeVisible();
  await expect(link).toBeVisible();
  await expect(link).toHaveAttribute("href", "/mock");
  await link.click();
  await expect(page).toHaveURL(/\/mock$/);
});
