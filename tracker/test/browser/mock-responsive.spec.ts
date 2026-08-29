import { expect, test, type Page } from "@playwright/test";

async function unlockAndStart(page: Page): Promise<void> {
  await page.request.delete("/api/mocks/current", {
    headers: { Authorization: "Bearer test-secret-key" },
  });
  await page.goto("/mock");
  await page.locator("[data-key]").fill("test-secret-key");
  await page.getByRole("button", { name: "Unlock mock" }).click();
  await expect(page.getByRole("heading", { name: "Start a mock draft" })).toBeVisible();
  await page.locator("[data-seed]").fill("8042");
  await page.locator("[data-user-slot]").selectOption("1");
  await page.getByRole("button", { name: "Start seeded mock" }).click();
  await expect(page.locator("[data-mock-workspace]")).toBeVisible();
}

test("the mock journey preserves one responsive board and compact disclosure state", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  await unlockAndStart(page);
  await expect(page.locator('.injury-badge[aria-label="Questionable injury status"]').first()).toBeVisible();

  const minimumDesktop = await page.evaluate(() => {
    const board = document.querySelector("[data-board-pane]")!.getBoundingClientRect();
    const rail = document.querySelector("[data-decision-rail]")!.getBoundingClientRect();
    const list = document.querySelector("[data-list]")!;
    return {
      boardLeft: board.left,
      boardRight: board.right,
      railLeft: rail.left,
      railRight: rail.right,
      noHorizontalOverflow: document.documentElement.scrollWidth <= innerWidth,
      listOwnsScroll: list.scrollHeight > list.clientHeight
        && getComputedStyle(list).overflowY === "auto",
    };
  });
  expect(minimumDesktop.noHorizontalOverflow).toBe(true);
  expect(minimumDesktop.listOwnsScroll).toBe(true);
  expect(minimumDesktop.boardLeft).toBeLessThan(minimumDesktop.railLeft);
  expect(minimumDesktop.boardRight).toBeLessThanOrEqual(minimumDesktop.railLeft + 1);
  expect(minimumDesktop.railRight).toBeLessThanOrEqual(1024);

  await page.locator("[data-player-search]").fill("Josh Allen");
  await page.locator('[data-player-key="k6"]').click();
  await expect(page.locator("[data-selected]")).toContainText("Josh Allen");
  await page.getByRole("button", { name: "Draft player" }).click();
  await expect(page.locator("[data-transition-status]")).toContainText("picks recorded");
  await expect(page.locator("[data-events-desktop]")).toContainText("CPU");
  await expect(page.locator("[data-status-revision]")).toHaveText("1");

  await page.getByRole("button", { name: "Pause" }).click();
  await expect(page.locator("[data-status-lifecycle]")).toHaveText("Paused");
  await page.getByRole("button", { name: "Resume" }).click();
  await expect(page.locator("[data-status-lifecycle]")).toHaveText("Active");
  await page.getByRole("button", { name: "Undo decision" }).click();
  await expect(page.locator("[data-status-revision]")).toHaveText("4");

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Restart from seed" }).click();
  await expect(page.locator("[data-status-revision]")).toHaveText("5");

  await page.setViewportSize({ width: 1440, height: 900 });
  const standardDesktop = await page.evaluate(() => {
    const screen = document.querySelector("[data-screen]")!.getBoundingClientRect();
    const statusTops = [...document.querySelectorAll(".mock-status > div")]
      .map((element) => Math.round(element.getBoundingClientRect().top));
    return {
      screenLeft: screen.left,
      screenRight: screen.right,
      screenWidth: screen.width,
      statusRows: new Set(statusTops).size,
      bodyScrolls: document.documentElement.scrollHeight > innerHeight,
    };
  });
  expect(standardDesktop.screenWidth).toBeLessThanOrEqual(1440);
  expect(standardDesktop.screenLeft).toBeGreaterThanOrEqual(0);
  expect(standardDesktop.screenRight).toBeLessThanOrEqual(1440);
  expect(standardDesktop.statusRows).toBe(1);
  expect(standardDesktop.bodyScrolls).toBe(false);

  await page.setViewportSize({ width: 1280, height: 650 });
  const shortDesktop = await page.evaluate(() => {
    const rail = document.querySelector("[data-decision-rail]")!;
    rail.scrollTop = rail.scrollHeight;
    const railBox = rail.getBoundingClientRect();
    const discard = document.querySelector("[data-discard]")!.getBoundingClientRect();
    return {
      railReachedEnd: rail.scrollHeight <= rail.clientHeight
        || Math.abs(rail.scrollTop + rail.clientHeight - rail.scrollHeight) <= 1,
      discardReachable: discard.top >= railBox.top && discard.bottom <= railBox.bottom + 1,
    };
  });
  expect(shortDesktop.railReachedEnd).toBe(true);
  expect(shortDesktop.discardReachable).toBe(true);

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator('.injury-badge[aria-label="Questionable injury status"]').first()).toBeVisible();
  await expect(page.locator("[data-pick-tools-toggle]")).toBeVisible();
  const compact = await page.evaluate(() => {
    const board = document.querySelector("[data-board-pane]")!.getBoundingClientRect();
    const rail = document.querySelector("[data-decision-rail]")!.getBoundingClientRect();
    const toggle = document.querySelector("[data-pick-tools-toggle]")!.getBoundingClientRect();
    const railProbe = document.elementFromPoint(250, rail.top + 28);
    return {
      sameColumn: Math.abs(board.left - rail.left) < 1,
      railBelowBoard: rail.top >= board.top,
      railOccludesBoardRows: railProbe?.closest(".rowA") === null,
      noHorizontalOverflow: document.documentElement.scrollWidth <= innerWidth,
      toggleTarget: toggle.height,
    };
  });
  expect(compact.sameColumn).toBe(true);
  expect(compact.railBelowBoard).toBe(true);
  expect(compact.railOccludesBoardRows).toBe(true);
  expect(compact.noHorizontalOverflow).toBe(true);
  expect(compact.toggleTarget).toBeGreaterThanOrEqual(44);
  await expect(page.locator("[data-pick-tools]")).toBeHidden();

  await page.locator("[data-pick-tools-toggle]").click();
  await expect(page.locator("[data-pick-tools]")).toBeVisible();
  await page.setViewportSize({ width: 1024, height: 768 });
  await expect(page.locator("[data-pick-tools-toggle]")).toBeHidden();
  await expect(page.locator("[data-pick-tools]")).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator("[data-pick-tools-toggle]")).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator("[data-pick-tools]")).toBeVisible();
});

test("saved-board recovery replaces the board and leaves only discard enabled", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  await page.goto("/mock");
  await page.locator("[data-key]").fill("test-recovery-key");
  await page.getByRole("button", { name: "Unlock mock" }).click();

  await expect(page.locator("[data-board-pane]")).toContainText("Saved board unavailable");
  await expect(page.locator("[data-decision-rail] button:enabled")).toHaveCount(1);
  await expect(page.locator("[data-discard]")).toBeEnabled();
  await expect(page.locator("[data-pick-tools-toggle]")).toBeHidden();

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator("[data-board-pane]")).toContainText("Saved board unavailable");
  await expect(page.locator("[data-decision-rail] button:enabled")).toHaveCount(1);
  await expect(page.locator("[data-pick-tools-toggle]")).toBeHidden();
});

test("compact disclosure keeps responding across a persisted page lifecycle", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  await unlockAndStart(page);
  await expect(page.locator("[data-pick-tools]")).toBeVisible();

  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pagehide", { persisted: true })));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true })));

  await expect(page.locator("[data-pick-tools-toggle]")).toBeVisible();
  await expect(page.locator("[data-pick-tools-toggle]")).toHaveAttribute("aria-expanded", "false");
  await expect(page.locator("[data-pick-tools]")).toBeHidden();
});
