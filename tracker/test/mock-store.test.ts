import { beforeEach, describe, expect, it } from "vitest";
import { env } from "cloudflare:test";
import { recordUserPick, startMock } from "../src/mock-draft";
import {
  appendMockTransition,
  discardCurrentMock,
  insertMock,
  loadCurrentMock,
  loadMock,
} from "../src/mock-store";
import { seededMarketStrategy } from "../src/mock-strategy";
import type { Board } from "../src/types";
import fixtureJson from "./fixtures/board.json";

const board = {
  ...fixtureJson,
  players: [
    ...fixtureJson.players,
    ...Array.from({ length: 12 * 15 - fixtureJson.players.length }, (_, index) => ({
      ...fixtureJson.players[0],
      key: `store-extra-${index}`,
      name: `Store Extra ${index}`,
      pos: ["QB", "RB", "WR", "TE", "K", "DEF"][index % 6],
      team: `T${index}`,
      rank: fixtureJson.players.length + index + 1,
      pos_rank: index + 10,
      adp: 140 + index,
      adp_rank: fixtureJson.players.length + index + 1,
    })),
  ],
} as Board;
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
      variance_preset: "realistic",
      revision: 3,
      next: { overall_pick: 4, team_name: "Brian", is_user: true },
    });
    expect(loaded?.aggregate.picks.map((pick) => pick.player_key)).toEqual(
      aggregate.picks.map((pick) => pick.player_key),
    );
    expect(loaded?.state).toMatchObject({
      configured: true,
      mock: {
        id: "mock-1",
        board_fingerprint: fingerprint,
        variance_preset: "realistic",
      },
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

    const replacement = startMock(shortBoard, { user_slot: 1, seed: 9 }, seededMarketStrategy);
    await insertMock(env.DB, replacement, {
      id: "mock-replacement",
      fingerprint,
      board_json: JSON.stringify(shortBoard),
      board: shortBoard,
    });

    expect((await loadCurrentMock(env.DB))?.state.mock?.id).toBe("mock-replacement");
    expect(await loadMock(env.DB, "mock-append")).toMatchObject({
      state: { mock: { id: "mock-append" }, complete: true },
      aggregate: { revision: 2, complete: true },
    });
  });

  it("returns to setup after discarding a replacement for a completed mock", async () => {
    const shortBoard = { ...board, num_teams: 2, roster_slots: { RB: 1 } };
    const completed = startMock(
      shortBoard,
      { user_slot: 1, seed: 8042 },
      seededMarketStrategy,
    );
    await insertMock(env.DB, completed, {
      id: "mock-completed",
      fingerprint,
      board_json: JSON.stringify(shortBoard),
      board: shortBoard,
    });
    const transition = recordUserPick(
      completed,
      shortBoard,
      shortBoard.players[0]!.key,
      seededMarketStrategy,
    );
    expect(
      await appendMockTransition(env.DB, "mock-completed", 0, transition),
    ).toBe("ok");

    const replacement = startMock(
      shortBoard,
      { user_slot: 1, seed: 9 },
      seededMarketStrategy,
    );
    await insertMock(env.DB, replacement, {
      id: "mock-replacement",
      fingerprint,
      board_json: JSON.stringify(shortBoard),
      board: shortBoard,
    });

    expect(await discardCurrentMock(env.DB, "mock-replacement")).toBe("ok");
    expect(await loadCurrentMock(env.DB)).toBeNull();
  });

  it("explicitly discards mock rows while retaining the deduplicated board snapshot", async () => {
    const aggregate = startMock(board, { user_slot: 1, seed: 7 }, seededMarketStrategy);
    await insertMock(env.DB, aggregate, {
      id: "mock-discard",
      fingerprint,
      board_json: boardJson,
      board,
    });

    expect(await discardCurrentMock(env.DB, "mock-discard")).toBe("ok");

    expect(await loadCurrentMock(env.DB)).toBeNull();
    expect(
      await env.DB
        .prepare("SELECT COUNT(*) AS count FROM mock_boards")
        .first<{ count: number }>(),
    ).toEqual({ count: 1 });
  });
});
