import { expect, test } from "@playwright/test";
import fixture from "../fixtures/board.json" with { type: "json" };
import { nextPick } from "../../src/draft";

for (const mode of ["live", "mock"]) {
  test(`${mode} explains waiting with readable advice on phone and desktop`, async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("ffb.trackerKey", "test-secret-key"));
    const teams = Array.from({ length: 10 }, (_, i) => ({ id: i + 1, draft_slot: i, name: `Team ${i}`, is_user: i === 0 }));
    const players = [
      { key: "te", name: "Scarce TE", pos: "TE", points: 140, adp: 3 },
      { key: "later", name: "Later TE", pos: "TE", points: 60, adp: 60 },
    ].map(p => ({ ...fixture.players[0], ...p, adp_high: null, adp_low: null, adp_stdev: null }));
    const board = { ...fixture, players, roster_slots: { TE: 1, BN: 3 } };
    const state = { configured: true, revision: 0, teams, picks: [], next: nextPick(teams, 4, 1), complete: false };
    await page.route("**/api/board", route => route.fulfill({ json: board }));
    await page.route("**/api/draft", route => route.fulfill({ json: { ...state, draft: { name: "Live", rounds: 4, team_count: 10 } } }));
    await page.route("**/api/mocks/current", route => route.fulfill({ json: { ...state, board, lifecycle: "active", can_undo: false,
      mock: { id: "test", board_fingerprint: "fixture", seed: 42, strategy_version: "market-need-v1", user_slot: 1, team_count: 10, rounds: 4, variance_preset: "realistic" } } }));
    for (const width of [390, 1024, 1440]) {
      await page.setViewportSize({ width, height: 844 });
      await page.goto(mode === "live" ? "/" : "/mock");
      await page.locator('[data-list] [data-player-key="te"]').click();
      await page.getByText("Take now or wait?", { exact: true }).click();
      const advice = page.locator("[data-wait-advice] p");
      await expect(advice).toBeVisible();
      await expect(advice).toContainText("Next turn #20: 18 opponent picks");
      await expect(advice).toContainText("80.0 lineup pts of positional drop-off");
      await expect(advice).toContainText("not a probability");
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      expect(await advice.evaluate(el => getComputedStyle(el).whiteSpace)).toBe("normal");
      await page.screenshot({ path: `/tmp/ffb-wait-${mode}-${width}.png` });
    }
  });
}
