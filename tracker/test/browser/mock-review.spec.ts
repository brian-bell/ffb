import { expect, test, type Page } from "@playwright/test";

async function unlockCompletedMock(page: Page): Promise<void> {
  await page.request.post("/api/test/complete", {
    headers: { Authorization: "Bearer test-secret-key" },
  });
  await page.goto("/mock");
  await page.locator("[data-key]").fill("test-secret-key");
  await page.getByRole("button", { name: "Unlock mock" }).click();
  await expect(page.locator("[data-mock-review]")).toBeVisible();
}

test("a completed mock reviews the drafted log, rosters, and config across refresh", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  await unlockCompletedMock(page);

  await expect(page.locator('[data-view="drafted"]')).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("[data-list] .picknote").first()).toBeVisible();

  const summary = page.locator("[data-review-summary]");
  await expect(summary).toContainText("Mock complete · 180 picks · 15 rounds · 12 teams");
  await expect(summary).toContainText("8042");
  await expect(summary).toContainText("Realistic");
  await expect(summary).toContainText("Slot 1");
  await expect(summary).toContainText("market-need-v1");

  await expect(page.locator("[data-review-rosters] details")).toHaveCount(12);
  const userRoster = page.locator("[data-review-rosters] details.user");
  await expect(userRoster).toHaveCount(1);
  await expect(userRoster).toHaveJSProperty("open", true);
  await expect(userRoster.locator("summary")).toContainText("Brian");
  await expect(userRoster.locator("summary")).toContainText("YOU");
  await expect(userRoster.locator(".review-pick")).toHaveCount(15);

  const cpuRoster = page.locator("[data-review-rosters] details:not(.user)").first();
  await cpuRoster.locator("summary").click();
  await expect(cpuRoster).toHaveJSProperty("open", true);
  await page.locator("[data-player-search]").fill("Prospect");
  await expect(cpuRoster).toHaveJSProperty("open", true);
  await expect(userRoster).toHaveJSProperty("open", true);
  await page.locator("[data-player-search]").fill("");
  await expect(cpuRoster).toHaveJSProperty("open", true);

  await expect(page.locator(".mock-review-live-link")).toBeVisible();
  await expect(page.locator(".mock-review-live-link")).toHaveAttribute("href", "/");

  await expect(page.locator("[data-lifecycle-toggle]")).toBeDisabled();
  await expect(page.locator("[data-suggestions]")).toBeHidden();
  await expect(page.locator("[data-pick-tools]")).toBeHidden();
  await expect(page.locator("[data-pick-tools-toggle]")).toBeHidden();
  await expect(page.getByRole("button", { name: "Replay this mock" })).toBeEnabled();
  await expect(page.getByRole("button", { name: "Start another mock" })).toBeEnabled();

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator("[data-pick-tools-toggle]")).toBeHidden();
  await expect(page.locator("[data-pick-tools]")).toBeHidden();
  await page.setViewportSize({ width: 1024, height: 768 });
  await expect(page.locator("[data-pick-tools-toggle]")).toBeHidden();
  await expect(page.locator("[data-pick-tools]")).toBeHidden();
  await expect(page.locator("[data-mock-review]")).toBeVisible();

  await page.reload();
  await expect(page.locator("[data-mock-review]")).toBeVisible();
  await expect(page.locator('[data-view="drafted"]')).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("[data-review-rosters] details")).toHaveCount(12);
});

test("saved-board recovery wins over review for a completed mock", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  await page.goto("/mock");
  await page.locator("[data-key]").fill("test-recovery-key");
  await page.getByRole("button", { name: "Unlock mock" }).click();

  await expect(page.locator("[data-board-pane]")).toContainText("Saved board unavailable");
  await expect(page.locator("[data-mock-review]")).toBeHidden();
  await expect(page.locator("[data-discard]")).toHaveText("Discard mock");
  await expect(page.locator("[data-reset]")).toHaveText("Restart from seed");
});

test("replay restarts the same mock from its saved seed", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  await unlockCompletedMock(page);

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Replay this mock" }).click();

  await expect(page.locator("[data-mock-review]")).toBeHidden();
  await expect(page.locator("[data-status-lifecycle]")).toHaveText("Active");
  await expect(page.locator("[data-status-seed]")).toHaveText("8042");
  await expect(page.locator("[data-status-revision]")).toHaveText("181");
  await expect(page.getByRole("button", { name: "Restart from seed" })).toBeVisible();
  await expect(page.locator("[data-suggestions]")).toBeVisible();
});

test("start another mock returns to the setup panel", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  await unlockCompletedMock(page);

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Start another mock" }).click();

  await expect(page.getByRole("heading", { name: "Start a mock draft" })).toBeVisible();
  await expect(page.locator("[data-mock-workspace]")).toBeHidden();
});

test("undo exits review back to the active workspace", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  await unlockCompletedMock(page);

  await page.getByRole("button", { name: "Undo decision" }).click();

  await expect(page.locator("[data-mock-review]")).toBeHidden();
  await expect(page.locator("[data-status-lifecycle]")).toHaveText("Active");
  await expect(page.locator("[data-suggestions]")).toBeVisible();
});

test("compact review keeps a single readable column with tappable rosters", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await unlockCompletedMock(page);

  const compact = await page.evaluate(() => {
    const roster = document.querySelector(".review-team summary")!.getBoundingClientRect();
    return {
      noHorizontalOverflow: document.documentElement.scrollWidth <= innerWidth,
      rosterTarget: roster.height,
    };
  });
  expect(compact.noHorizontalOverflow).toBe(true);
  expect(compact.rosterTarget).toBeGreaterThanOrEqual(44);
  await expect(page.locator("[data-review-summary]")).toBeVisible();

  const reachable = await page.evaluate(() => {
    const screen = document.querySelector("[data-screen]")!;
    screen.scrollTop = screen.scrollHeight;
    const discard = document.querySelector("[data-discard]")!.getBoundingClientRect();
    return {
      screenUserScrollable: getComputedStyle(screen).overflowY === "auto",
      discardReachable: discard.top >= 0 && discard.bottom <= innerHeight + 1,
    };
  });
  expect(reachable.screenUserScrollable).toBe(true);
  expect(reachable.discardReachable).toBe(true);
});
