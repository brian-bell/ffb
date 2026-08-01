import { beforeEach, describe, expect, it } from "vitest";
import { env } from "cloudflare:test";
import { recordUserPick, startMock } from "../src/mock-draft";
import {
  appendMockTransition,
  discardCurrentMock,
  insertMock,
  loadCurrentMock,
  loadMock,
  resetCurrentMock,
  setMockPaused,
  undoLatestMockDecision,
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
      env.DB.prepare("DELETE FROM mock_checkpoints"),
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
      lifecycle: "active",
      can_undo: false,
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
    expect(await env.DB.prepare(
      `SELECT decision_overall_pick, pick_count_before, rng_state_before
         FROM mock_checkpoints WHERE mock_id = ?`,
    ).bind("mock-append").all()).toMatchObject({
      results: [{
        decision_overall_pick: transition.checkpoint.decision_overall_pick,
        pick_count_before: transition.checkpoint.pick_count_before,
        rng_state_before: transition.checkpoint.rng_state_before,
      }],
    });

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

    expect(await discardCurrentMock(env.DB, "mock-replacement", 0)).toBe("ok");
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

    expect(await discardCurrentMock(env.DB, "mock-discard", 0)).toBe("ok");

    expect(await loadCurrentMock(env.DB)).toBeNull();
    expect(
      await env.DB
        .prepare("SELECT COUNT(*) AS count FROM mock_boards")
        .first<{ count: number }>(),
      ).toEqual({ count: 1 });
  });

  it("pauses and resumes without changing the saved turn, picks, or RNG state", async () => {
    const aggregate = startMock(board, { user_slot: 4, seed: 8042 }, seededMarketStrategy);
    await insertMock(env.DB, aggregate, {
      id: "mock-pause",
      fingerprint,
      board_json: boardJson,
      board,
    });

    expect(await setMockPaused(env.DB, "mock-pause", 3, true)).toBe("ok");
    const paused = await loadCurrentMock(env.DB);
    expect(paused?.state).toMatchObject({
      lifecycle: "paused",
      revision: 4,
      next: { overall_pick: 4, team_name: "Brian" },
    });
    expect(paused?.aggregate).toMatchObject({
      rng_state: aggregate.rng_state,
      picks: aggregate.picks,
    });
    expect(await setMockPaused(env.DB, "mock-pause", 4, true)).toBe("invalid_state");
    expect(await setMockPaused(env.DB, "mock-pause", 3, false)).toBe("stale_mock");
    expect(await setMockPaused(env.DB, "mock-pause", 4, false)).toBe("ok");
    expect((await loadCurrentMock(env.DB))?.state).toMatchObject({
      lifecycle: "active",
      revision: 5,
      next: { overall_pick: 4, team_name: "Brian" },
    });
  });

  it("undoes a complete user decision and restores its pre-decision RNG and turn", async () => {
    const shortBoard = { ...board, num_teams: 2, roster_slots: { RB: 1 } };
    const aggregate = startMock(shortBoard, { user_slot: 1, seed: 8042 }, seededMarketStrategy);
    await insertMock(env.DB, aggregate, {
      id: "mock-undo",
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
    expect(await appendMockTransition(env.DB, "mock-undo", 0, transition)).toBe("ok");
    expect((await loadCurrentMock(env.DB))?.state).toMatchObject({
      lifecycle: "complete",
      can_undo: true,
      revision: 2,
    });

    expect(await undoLatestMockDecision(env.DB, "mock-undo", 2)).toBe("ok");
    const undone = await loadCurrentMock(env.DB);
    expect(undone?.state).toMatchObject({
      lifecycle: "active",
      can_undo: false,
      revision: 3,
      picks: [],
      next: { overall_pick: 1, team_name: "Brian" },
    });
    expect(undone?.aggregate.rng_state).toBe(aggregate.rng_state);
    expect(await undoLatestMockDecision(env.DB, "mock-undo", 3)).toBe("no_user_decisions");
  });

  it("keeps a paused mock paused when undoing its latest user decision", async () => {
    const aggregate = startMock(board, { user_slot: 4, seed: 8042 }, seededMarketStrategy);
    await insertMock(env.DB, aggregate, {
      id: "mock-paused-undo",
      fingerprint,
      board_json: boardJson,
      board,
    });
    const selected = board.players.find(
      (player) => !aggregate.picks.some((pick) => pick.player_key === player.key),
    )!;
    const transition = recordUserPick(aggregate, board, selected.key, seededMarketStrategy);
    expect(await appendMockTransition(env.DB, "mock-paused-undo", 3, transition)).toBe("ok");
    const advancedRevision = 3 + transition.appended_picks.length;
    expect(await setMockPaused(env.DB, "mock-paused-undo", advancedRevision, true)).toBe("ok");
    expect(await undoLatestMockDecision(env.DB, "mock-paused-undo", advancedRevision + 1))
      .toBe("ok");
    expect((await loadCurrentMock(env.DB))?.state).toMatchObject({
      lifecycle: "paused",
      can_undo: false,
      revision: advancedRevision + 2,
      picks: aggregate.picks,
      next: aggregate.next,
    });
  });

  it("resets deterministically while preserving identity and advancing revision", async () => {
    const aggregate = startMock(board, { user_slot: 4, seed: 8042 }, seededMarketStrategy);
    await insertMock(env.DB, aggregate, {
      id: "mock-reset",
      fingerprint,
      board_json: boardJson,
      board,
    });
    const transition = recordUserPick(
      aggregate,
      board,
      board.players.find((player) => !aggregate.picks.some((pick) => pick.player_key === player.key))!.key,
      seededMarketStrategy,
    );
    expect(await appendMockTransition(env.DB, "mock-reset", 3, transition)).toBe("ok");
    const advancedRevision = 3 + transition.appended_picks.length;
    const restarted = startMock(board, { user_slot: 4, seed: 8042 }, seededMarketStrategy);

    expect(await resetCurrentMock(env.DB, "mock-reset", advancedRevision, restarted)).toBe("ok");
    const reset = await loadCurrentMock(env.DB);
    expect(reset?.state).toMatchObject({
      mock: { id: "mock-reset", board_fingerprint: fingerprint },
      lifecycle: "active",
      can_undo: false,
      revision: advancedRevision + 1,
      next: { overall_pick: 4, team_name: "Brian" },
    });
    expect(reset?.aggregate.picks.map((pick) => pick.player_key)).toEqual(
      aggregate.picks.map((pick) => pick.player_key),
    );
  });
});
