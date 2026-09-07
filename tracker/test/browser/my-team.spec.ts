import { expect, test } from "@playwright/test";

test("My team shows only the user's pick snapshots newest first with position filters", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript(() => localStorage.setItem("ffb.trackerKey", "test-secret-key"));
  await page.route("**/api/draft", route => route.fulfill({ json: {
    configured: true, revision: 3, complete: false,
    draft: { name: "Live draft", rounds: 8, team_count: 2 },
    teams: [
      { id: 1, name: "Brian", draft_slot: 0, is_user: true },
      { id: 2, name: "Other", draft_slot: 1, is_user: false },
    ],
    picks: [
      { overall_pick: 1, team_id: 1, team_name: "Brian", player_key: "k0", player_name: "Christian McCaffrey", player_pos: "RB", player_team: "SFO" },
      { overall_pick: 2, team_id: 2, team_name: "Other", player_key: "k1", player_name: "Ja'Marr Chase", player_pos: "WR", player_team: "CIN" },
      { overall_pick: 3, team_id: 1, team_name: "Brian", player_key: "manual:te", player_name: "My off-board TE", player_pos: "TE", player_team: null },
    ].map(pick => ({ ...pick, round: 1, round_pick: pick.overall_pick, picked_at: "" })),
    next: { overall_pick: 4, round: 2, round_pick: 2, team_id: 1, team_name: "Brian", is_user: true, direction: "reverse" },
  } }));
  await page.goto("/");
  await page.getByRole("button", { name: "My team", exact: true }).click();
  const list = page.locator("[data-list]");
  await expect(list.locator(".rowA")).toHaveCount(2);
  await expect(list.locator(".rowA").first()).toHaveCSS("opacity", "1");
  await expect(list.locator(".rowA").first()).toHaveCSS("text-decoration-line", "none");
  await expect(list.locator(".rowA").first()).toContainText("My off-board TE");
  await expect(list).toContainText("Christian McCaffrey");
  await expect(list).not.toContainText("Ja'Marr Chase");
  await expect(list.locator("button[data-player-key]")).toHaveCount(0);
  await expect(page.locator("[data-player-search]")).toBeHidden();
  await page.getByRole("tab", { name: "QB", exact: true }).click();
  await expect(list).toContainText("No QB players on your team yet.");
  await page.getByRole("tab", { name: "RB", exact: true }).click();
  await expect(list.locator(".rowA")).toHaveCount(1);
  await expect(list).toContainText("Christian McCaffrey");
  await page.getByRole("tab", { name: "ALL", exact: true }).click();
  await page.getByRole("button", { name: "Drafted", exact: true }).click();
  await expect(list.locator(".rowA")).toHaveCount(3);
  await page.getByRole("button", { name: "Available", exact: true }).click();
  await expect(page.locator("[data-player-search]")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole("button", { name: "My team", exact: true }).click();
  await page.screenshot({ path: "/tmp/ffb-my-team.png" });
});

test("My team explains when no draft is configured", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("ffb.trackerKey", "test-secret-key"));
  await page.route("**/api/draft", route => route.fulfill({ json: { configured: false, picks: [], revision: 0 } }));
  await page.goto("/");
  await page.getByRole("button", { name: "My team", exact: true }).click();
  await expect(page.locator("[data-list]")).toContainText("Set up your draft to see your team.");
});
