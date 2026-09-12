import type { Board } from "../../src/types";
import { expect, test } from "@playwright/test";
import fixture from "../fixtures/board.json" with { type: "json" };

const teams = [
  { id: 1, name: "Brian", draft_slot: 0, is_user: true },
  { id: 2, name: "Other", draft_slot: 1, is_user: false },
];
const picks = [0, 1, 2, 3].map((index) => {
  const p = fixture.players[index]!;
  const team = teams[index === 0 || index === 3 ? 0 : 1]!;
  return { overall_pick: index + 1, round: Math.floor(index / 2) + 1, round_pick: index % 2 + 1,
    team_id: team.id, team_name: team.name, player_key: p.key, player_name: p.name,
    player_pos: p.pos, player_team: p.team, picked_at: "2026-09-06T00:00:00Z" };
});
const saved = { configured: true, complete: true, next: null, revision: 4,
  draft: { name: "Original", rounds: 2, team_count: 2 }, teams, picks };

test("replay uses the live board client and latest board without sending draft writes", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript(() => localStorage.setItem("ffb.trackerKey", "test-secret-key"));
  const writes: string[] = [];
  let board: Board = structuredClone(fixture) as Board;
  await page.route("**/api/**", route => {
    const request = route.request();
    if (request.method() !== "GET") writes.push(request.method() + " " + request.url());
    return route.fulfill({ json: request.url().endsWith("/api/board") ? board : saved });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Board settings and API key" }).click();
  await page.getByRole("button", { name: "Replay this draft" }).click();
  await expect(page.locator("[data-replay-progress]")).toHaveText("0 / 4 picks replayed");
  await expect(page.locator("[data-list] .rowA").first()).toContainText(fixture.players[0]!.name);
  await page.getByRole("button", { name: "Next saved pick", exact: true }).click();
  await expect(page.locator("[data-list] .rowA").filter({ hasText: picks[0]!.player_name })).toHaveCount(0);
  await page.getByRole("button", { name: "My team", exact: true }).click();
  await expect(page.locator("[data-list]")).toContainText(picks[0]!.player_name);
  await page.getByRole("button", { name: "Previous saved pick", exact: true }).click();
  await expect(page.locator("[data-list]")).not.toContainText(picks[0]!.player_name);
  await page.getByRole("button", { name: "Next my pick", exact: true }).click();
  await expect(page.locator("[data-replay-progress]")).toHaveText("3 / 4 picks replayed");
  board = { ...board, generated_at: "2026-09-12T12:00:00Z" };
  await page.reload();
  await expect(page.locator("[data-replay-progress]")).toHaveText("3 / 4 picks replayed");
  await expect(page.locator("[data-foot]")).toContainText("9/12/2026");
  board = { ...board, players: board.players.map(p => p.key === "k6" ? { ...p, points: 999 } : p) };
  await page.getByRole("button", { name: "Refresh board", exact: true }).click();
  await expect(page.locator("[data-list] .rowA").filter({ hasText: "Josh Allen" })).toContainText("999.0");
  await expect(page.locator("[data-replay-progress]")).toHaveText("3 / 4 picks replayed");
  await page.getByRole("button", { name: "Next saved pick", exact: true }).click();
  await expect(page.locator("[data-replay-progress]")).toHaveText("4 / 4 picks replayed");
  await expect(page.getByRole("button", { name: "Next saved pick", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Exit replay", exact: true }).click();
  await expect(page.locator("[data-replay]")).toBeHidden();
  await expect(page.locator("[data-clock]")).toHaveText("Draft complete");
  expect(writes).toEqual([]);
});

test("imports archived picks without their old board and rejects malformed files", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("ffb.trackerKey", "test-secret-key"));
  await page.route("**/api/board", route => route.fulfill({ json: fixture }));
  await page.route("**/api/draft", route => route.fulfill({ json: { configured: false, picks: [], revision: 0 } }));
  await page.goto("/");
  await page.getByRole("button", { name: "Board settings and API key" }).click();
  const file = page.locator("[data-import-replay]");
  const upload = async (contents: string) => file.evaluate((input, text) => {
    const transfer = new DataTransfer();
    transfer.items.add(new File([text], "draft.json", { type: "application/json" }));
    (input as HTMLInputElement).files = transfer.files;
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }, contents);
  await upload("{}");
  await expect(page.locator("[data-replay-import-error]")).toContainText("Saved draft must contain");
  await expect(page.locator("[data-replay]")).toBeHidden();
  await upload(JSON.stringify(saved));
  await expect(page.locator("[data-replay-progress]")).toHaveText("0 / 4 picks replayed");
  await expect(page.locator("[data-setup-modal]")).toBeHidden();
  await page.getByRole("button", { name: "Next saved pick", exact: true }).click();
  await expect(page.locator("[data-replay-progress]")).toHaveText("1 / 4 picks replayed");
});

test("cannot enter replay while a live pick write is pending", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("ffb.trackerKey", "test-secret-key"));
  const current = { ...saved, complete: false, picks: [], revision: 0,
    next: { overall_pick: 1, round: 1, round_pick: 1, team_id: 1, team_name: "Brian", is_user: true, direction: "forward" } };
  let releaseWrite!: () => void;
  const pending = new Promise<void>(resolve => { releaseWrite = resolve; });
  await page.route("**/api/board", route => route.fulfill({ json: fixture }));
  await page.route("**/api/draft", route => route.fulfill({ json: current }));
  await page.route("**/api/picks", async route => {
    await pending;
    await route.fulfill({ json: { ...current, picks: [picks[0]], revision: 1 } });
  });
  await page.goto("/");
  await page.locator('[data-player-key="k0"]').click();
  const writing = page.waitForRequest(request => request.url().endsWith("/api/picks"));
  await page.locator("[data-record-pick]").click();
  await writing;
  await page.getByRole("button", { name: "Board settings and API key" }).click();
  // Also exercise the entry guard after a file read finishes during a pending write.
  await page.locator("[data-import-replay]").evaluate((input, contents) => {
    const transfer = new DataTransfer();
    transfer.items.add(new File([contents], "draft.json", { type: "application/json" }));
    (input as HTMLInputElement).files = transfer.files;
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }, JSON.stringify(saved));
  await expect(page.locator("[data-replay-import-error]")).toContainText("Wait for the current draft update");
  await expect(page.locator("[data-import-replay]")).toBeDisabled();
  releaseWrite();
  await expect(page.locator("[data-import-replay]")).toBeEnabled();
  await expect(page.locator("[data-replay]")).toBeHidden();
});

test("keeps replay isolated while exiting to a slow live draft", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("ffb.trackerKey", "test-secret-key"));
  let leaving = false;
  let finishExit!: () => void;
  const pending = new Promise<void>(resolve => { finishExit = resolve; });
  await page.route("**/api/board", route => route.fulfill({ json: fixture }));
  await page.route("**/api/draft", async route => {
    if (leaving) await pending;
    await route.fulfill({ json: saved });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Board settings and API key" }).click();
  await page.getByRole("button", { name: "Replay this draft" }).click();
  leaving = true;
  const request = page.waitForRequest(req => req.url().endsWith("/api/draft"));
  await page.getByRole("button", { name: "Exit replay", exact: true }).click();
  await request;
  await page.getByRole("button", { name: "Board settings and API key" }).click();
  await expect(page.locator("[data-import-replay]")).toBeDisabled();
  await expect(page.locator("[data-replay-progress]")).toHaveText("0 / 4 picks replayed");
  await expect(page.locator("[data-pick-panel]")).toBeHidden();
  finishExit();
  await expect(page.locator("[data-replay]")).toBeHidden();
  await expect(page.locator("[data-clock]")).toHaveText("Draft complete");
});

test("ignores a live draft response that arrives after a replay import", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("ffb.trackerKey", "test-secret-key"));
  let finishRead!: () => void;
  const pending = new Promise<void>(resolve => { finishRead = resolve; });
  await page.route("**/api/board", route => route.fulfill({ json: fixture }));
  await page.route("**/api/draft", async route => {
    await pending;
    await route.fulfill({ json: saved });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Board settings and API key" }).click();
  await page.locator("[data-import-replay]").evaluate((input, contents) => {
    const transfer = new DataTransfer();
    transfer.items.add(new File([contents], "draft.json", { type: "application/json" }));
    (input as HTMLInputElement).files = transfer.files;
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }, JSON.stringify(saved));
  await expect(page.locator("[data-replay-progress]")).toHaveText("0 / 4 picks replayed");
  const response = page.waitForResponse(res => res.url().endsWith("/api/draft"));
  finishRead();
  await (await response).finished();
  await page.getByRole("button", { name: "My team", exact: true }).click();
  await expect(page.locator("[data-list]")).toContainText("You haven’t drafted any players yet.");
  await expect(page.locator("[data-replay-progress]")).toHaveText("0 / 4 picks replayed");
});
