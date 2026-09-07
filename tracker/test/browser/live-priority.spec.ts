import { expect, test } from "@playwright/test";
import fixture from "../fixtures/board.json" with { type: "json" };
import type { DraftState, RecordedPick } from "../../src/draft-store";

test("own picks and undo update starter priority and bye warnings immediately", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript(() => localStorage.setItem("ffb.trackerKey", "test-secret-key"));
  const players = [
    { key: "qb-a", name: "First QB", pos: "QB", rank: 1, bye: 7 },
    { key: "qb-b", name: "Backup QB", pos: "QB", rank: 2, bye: 7 },
    { key: "rb-clash", name: "Same Bye RB", pos: "RB", rank: 3, bye: 8 },
    { key: "rb-clear", name: "Different Bye RB", pos: "RB", rank: 4, bye: 9 },
    { key: "rb-owned", name: "Owned RB", pos: "RB", rank: 5, bye: 8 },
  ].map(player => ({ ...fixture.players[0]!, ...player }));
  const initialPick: RecordedPick = {
    overall_pick: 1, round: 1, round_pick: 1, team_id: 1, team_name: "Brian",
    player_key: "rb-owned", player_name: "Owned RB", player_pos: "RB", player_team: "SFO", picked_at: "",
  };
  const initial: DraftState = {
    configured: true, revision: 1, complete: false,
    draft: { name: "Live draft", rounds: 8, team_count: 2 },
    teams: [
      { id: 1, name: "Brian", draft_slot: 0, is_user: true },
      { id: 2, name: "Other", draft_slot: 1, is_user: false },
    ],
    picks: [initialPick],
    next: { overall_pick: 2, round: 1, round_pick: 2, team_id: 1, team_name: "Brian", is_user: true, direction: "forward" },
  };
  let state = initial;
  await page.route("**/api/board", route => route.fulfill({ json: { ...fixture, players } }));
  await page.route("**/api/draft", route => route.fulfill({ json: state }));
  await page.route("**/api/picks", route => {
    expect(route.request().postDataJSON().player_key).toBe("qb-a");
    state = {
      ...initial, revision: 2,
      picks: [...initial.picks, { ...initialPick, overall_pick: 2, round_pick: 2, player_key: "qb-a", player_name: "First QB", player_pos: "QB" }],
      next: { ...initial.next!, overall_pick: 3, team_id: 2, team_name: "Other", is_user: false },
    };
    return route.fulfill({ json: state });
  });
  await page.route("**/api/picks/latest", route => {
    state = initial;
    return route.fulfill({ json: state });
  });
  await page.goto("/");
  const rows = page.locator("[data-list] .rowA");
  const needs = page.locator("[data-starter-needs]");
  await expect(rows.first()).toContainText("First QB");
  await expect(needs).toContainText("QB 1");
  await expect(needs).toContainText("WR 1 · TE 1 · WR/TE 1 · FLEX 1");
  await expect(page.locator('[data-player-key="rb-clash"]')).toContainText("Bye clash");
  await page.locator('[data-player-key="qb-a"]').click();
  await page.locator("[data-record-pick]").click();
  await expect(needs).not.toContainText("QB 1");
  await expect(rows.first()).toContainText("Different Bye RB");
  await expect(page.locator('[data-player-key="qb-b"]')).toContainText("Bye clash");
  await expect(page.locator('[data-player-key="qb-a"]')).toHaveCount(0);
  await page.locator("[data-pick-tools-toggle]").click();
  await page.locator("[data-undo-pick]").click();
  await expect(needs).toContainText("QB 1");
  await expect(rows.first()).toContainText("First QB");
  await expect(page.locator('[data-player-key="qb-b"]')).not.toContainText("Bye clash");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: "/tmp/ffb-starter-priority.png" });
});
