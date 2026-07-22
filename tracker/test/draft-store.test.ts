import { beforeEach, describe, expect, it } from "vitest";
import { env } from "cloudflare:test";
import { configureDraft, getDraftState, recordPick, undoLatestPick } from "../src/draft-store";

describe("draft store", () => {
  beforeEach(async () => {
    await env.DB.batch([
      env.DB.prepare("DELETE FROM picks WHERE draft_id = 1"),
      env.DB.prepare("DELETE FROM teams WHERE draft_id = 1"),
      env.DB.prepare("DELETE FROM drafts WHERE id = 1"),
    ]);
  });

  it("persists first-round team order and derives the initial clock", async () => {
    await configureDraft(env.DB, {
      name: "Home League",
      rounds: 2,
      teams: [
        { name: "Brian", is_user: true },
        { name: "Team Two", is_user: false },
      ],
    });

    await expect(getDraftState(env.DB)).resolves.toMatchObject({
      configured: true,
      draft: { name: "Home League", rounds: 2, team_count: 2 },
      teams: [
        { name: "Brian", draft_slot: 0, is_user: true },
        { name: "Team Two", draft_slot: 1, is_user: false },
      ],
      picks: [],
      next: { overall_pick: 1, team_name: "Brian", direction: "forward" },
      complete: false,
      revision: 0,
    });
  });

  it("records a player snapshot and only undoes the current latest pick", async () => {
    await configureDraft(env.DB, {
      name: "Home League",
      rounds: 2,
      teams: [
        { name: "Brian", is_user: true },
        { name: "Team Two", is_user: false },
      ],
    });
    await recordPick(env.DB, { key: "p1", name: "Player One", pos: "RB", team: "BUF" }, 1);
    await expect(getDraftState(env.DB)).resolves.toMatchObject({
      picks: [{ overall_pick: 1, player_key: "p1", player_name: "Player One", team_name: "Brian" }],
      next: { overall_pick: 2, team_name: "Team Two" },
    });
    expect(await undoLatestPick(env.DB, 2)).toBe("stale_draft");
    expect(await undoLatestPick(env.DB, 1)).toBe("ok");
    await expect(getDraftState(env.DB)).resolves.toMatchObject({ picks: [], next: { overall_pick: 1 } });
  });
});
