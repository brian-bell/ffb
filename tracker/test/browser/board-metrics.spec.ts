import { expect, test } from "@playwright/test";

for (const mode of ["live", "mock"] as const) {
  test(`${mode} metrics stay labeled, aligned, and keyboard accessible across viewports`, async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("ffb.trackerKey", "test-secret-key"));
    if (mode === "mock") {
      await page.request.delete("/api/mocks/current", { headers: { Authorization: "Bearer test-secret-key" } });
      await page.goto("/mock");
      await page.getByRole("button", { name: "Start seeded mock" }).click();
    } else {
      await page.route("**/api/draft", route => route.fulfill({ json: {
        configured: true, revision: 0, complete: false,
        draft: { name: "Live draft", rounds: 16, team_count: 2 },
        teams: [
          { id: 1, name: "Brian", draft_slot: 0, is_user: true },
          { id: 2, name: "Other", draft_slot: 1, is_user: false },
        ],
        picks: [],
        next: { overall_pick: 1, round: 1, round_pick: 1, team_id: 1, team_name: "Brian", is_user: true, direction: "forward" },
      } }));
      await page.goto("/");
    }
    const first = page.locator("[data-list] .rowA").first();
    await expect(first).toContainText("Christian McCaffrey");
    await expect(first.locator(".points")).toHaveText("Projected points: 306.7");
    await expect(first.locator(".vorp")).toHaveText("VORP: 120.0");
    await expect(first.locator(".adp")).toHaveText("ADP: 1.4");
    await expect(first).toHaveAccessibleName(/Projected points: 306.7.*VORP: 120.0.*ADP: 1.4/);

    for (const width of [320, 390, 768, 1024, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      await expect(page.locator(".h-points")).toBeVisible();
      const geometry = await first.evaluate(row => {
        const cells = [".points", ".vorp", ".adp"].map(selector => {
          const cell = row.querySelector(selector)!;
          const box = cell.getBoundingClientRect();
          const range = document.createRange();
          const value = selector === ".vorp" ? cell.querySelector(".tnum")! : cell;
          range.selectNodeContents(value.lastChild!);
          const text = range.getBoundingClientRect();
          return { left: box.left, right: box.right, top: box.top, bottom: box.bottom, textRight: text.right };
        });
        const name = row.querySelector(".nm")!.getBoundingClientRect();
        const headers = [".h-points", ".h-vorp", ".h-adp"].map(selector => document.querySelector(selector)!.getBoundingClientRect().right);
        return { cells, headers, nameBottom: name.bottom, overflow: document.documentElement.scrollWidth > innerWidth };
      });
      expect(geometry.overflow).toBe(false);
      geometry.cells.forEach((cell, index) => {
        expect(cell.textRight).toBeLessThanOrEqual(cell.right + 1);
        expect(Math.abs(cell.right - geometry.headers[index]!)).toBeLessThanOrEqual(1);
        if (index > 0) expect(cell.left).toBeGreaterThanOrEqual(geometry.cells[index - 1]!.right);
        if (width <= 600) expect(cell.top).toBeGreaterThanOrEqual(geometry.nameBottom);
      });
      await first.focus();
      await page.keyboard.press("Enter");
      await expect(first).toHaveAttribute("aria-pressed", "true");
      await page.screenshot({ path: `/tmp/ffb-${mode}-metrics-${width}.png` });
      await first.focus();
      await page.keyboard.press("Enter");
      await expect(first).toHaveAttribute("aria-pressed", "false");
    }
    const guide = page.locator(".board-guide");
    await guide.locator("summary").focus();
    await page.keyboard.press("Enter");
    await expect(guide).toHaveAttribute("open", "");
    await expect(guide).toContainText("points above positional replacement");
    await expect(guide).toContainText(mode === "live" ? "Live Available prioritizes roster needs" : "Mock Available follows board rank");
    await expect(guide).toContainText("Rows are not sorted by projected points alone");
    await page.locator("[data-player-search]").fill("San Francisco Defense");
    const missing = page.locator("[data-list] .rowA").first();
    await expect(missing.locator(".points")).toHaveText("Projected points unavailable—");
    await expect(missing).toHaveAccessibleName(/Projected points unavailable/);
  });
}
