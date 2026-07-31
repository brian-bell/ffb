import { beforeEach, describe, expect, it } from "vitest";
import { env } from "cloudflare:test";
import { recordUserPick, startMock } from "../src/mock-draft";
import {
  appendMockTransition,
  discardCurrentMock,
  insertMock,
  loadCurrentMock,
} from "../src/mock-store";
import { seededMarketStrategy } from "../src/mock-strategy";
import type { Board } from "../src/types";
import fixtureJson from "./fixtures/board.json";

const board = fixtureJson as Board;
const boardJson = JSON.stringify(board);
const fingerprint = "a".repeat(64);

describe("mock store", () => {
  beforeEach(async () => {
    await env.DB.batch([
      env.DB.prepare("DELETE FROM mock_picks"),
      env.DB.prepare("DELETE FROM mock_teams"),
      env.DB.prepare("DELETE FROM mock_drafts"),
      env.DB.prepare("DELETE FROM mock_boards"),
    ]);
  });

  it("round-trips an isolated aggregate and its immutable board snapshot", async () => {
    const aggregate = startMock(board, { user_slot: 4, seed: 8042 }, seededMarketStrategy);

    await insertMock(env.DB, aggregate, {
      id: "mock-1",
      fingerprint,
      board_json: boardJson,
      board,
    });

    const loaded = await loadCurrentMock(env.DB);
    expect(loaded?.board_json).toBe(boardJson);
    expect(loaded?.aggregate).toMatchObject({
      seed: 8042,
      strategy_version: "seeded-market-v0",
      user_slot: 4,
      team_count: 12,
      rounds: 15,
      revision: 3,
      next: { overall_pick: 4, team_name: "Brian", is_user: true },
    });
    expect(loaded?.aggregate.picks.map((pick) => pick.player_key)).toEqual(
      aggregate.picks.map((pick) => pick.player_key),
    );
    expect(loaded?.state).toMatchObject({
      configured: true,
      mock: { id: "mock-1", board_fingerprint: fingerprint },
      revision: 3,
    });
  });

  it("atomically appends a multi-pick transition and rejects a stale replay", async () => {
    const shortBoard = { ...board, num_teams: 2, roster_slots: { RB: 1 } };
    const aggregate = startMock(shortBoard, { user_slot: 1, seed: 8042 }, seededMarketStrategy);
    await insertMock(env.DB, aggregate, {
      id: "mock-append",
      fingerprint,
      board_json: JSON.stringify(shortBoard),
      board: shortBoard,
    });
    const transition = recordUserPick(
      aggregate,
      shortBoard,
      shortBoard.players[0]!.key,
      seededMarketStrategy,
    );

    expect(
      await appendMockTransition(env.DB, "mock-append", 0, transition),
    ).toBe("ok");
    expect(
      await appendMockTransition(env.DB, "mock-append", 0, transition),
    ).toBe("stale_mock");

    const loaded = await loadCurrentMock(env.DB);
    expect(loaded?.aggregate).toMatchObject({
      revision: 2,
      rng_state: transition.next_rng_state,
      complete: true,
    });
    expect(loaded?.aggregate.picks).toHaveLength(2);
  });

  it("explicitly discards mock rows while retaining the deduplicated board snapshot", async () => {
    const aggregate = startMock(board, { user_slot: 1, seed: 7 }, seededMarketStrategy);
    await insertMock(env.DB, aggregate, {
      id: "mock-discard",
      fingerprint,
      board_json: boardJson,
      board,
    });

    await discardCurrentMock(env.DB);

    expect(await loadCurrentMock(env.DB)).toBeNull();
    expect(
      await env.DB
        .prepare("SELECT COUNT(*) AS count FROM mock_boards")
        .first<{ count: number }>(),
    ).toEqual({ count: 1 });
  });
});
