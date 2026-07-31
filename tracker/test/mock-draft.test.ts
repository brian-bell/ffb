import { describe, expect, it } from "vitest";
import { recordUserPick, startMock } from "../src/mock-draft";
import { buildPlayerPool } from "../src/player-pool";
import { seededMarketStrategy, type OpponentStrategy } from "../src/mock-strategy";
import type { Board } from "../src/types";
import fixtureJson from "./fixtures/board.json";

const board = {
  ...fixtureJson,
  players: [
    ...fixtureJson.players,
    ...Array.from({ length: 8 }, (_, index) => ({
      ...fixtureJson.players[0],
      key: `extra-${index}`,
      name: `Extra Player ${index}`,
      rank: fixtureJson.players.length + index + 1,
      pos_rank: index + 10,
      adp: 140 + index,
      adp_rank: fixtureJson.players.length + index + 1,
    })),
  ],
} as Board;

const unusedStrategy: OpponentStrategy = {
  version: "unused",
  choose() {
    throw new Error("slot 1 must not ask the opponent strategy to choose");
  },
};

describe("mock draft", () => {
  it("starts slot 1 with Brian on the first pick and no simulated picks", () => {
    const mock = startMock(board, { user_slot: 1, seed: 8042 }, unusedStrategy);

    expect(mock).toMatchObject({
      seed: 8042,
      user_slot: 1,
      team_count: 12,
      rounds: 15,
      picks: [],
      next: {
        overall_pick: 1,
        round: 1,
        round_pick: 1,
        team_name: "Brian",
        is_user: true,
      },
      complete: false,
      revision: 0,
      rng_state: 8042,
    });
    expect(mock.teams).toHaveLength(12);
    expect(mock.teams[0]).toEqual({ id: 1, name: "Brian", draft_slot: 0, is_user: true });
  });

  it("reproducibly simulates legal picks before Brian's slot 4 turn", () => {
    const first = startMock(board, { user_slot: 4, seed: 8042 }, seededMarketStrategy);
    const replay = startMock(board, { user_slot: 4, seed: 8042 }, seededMarketStrategy);

    expect(first.picks).toHaveLength(3);
    expect(first.picks.map((pick) => pick.overall_pick)).toEqual([1, 2, 3]);
    expect(first.picks.every((pick) => pick.source === "simulated")).toBe(true);
    expect(new Set(first.picks.map((pick) => pick.player_key)).size).toBe(3);
    expect(first.next).toMatchObject({
      overall_pick: 4,
      round: 1,
      round_pick: 4,
      team_name: "Brian",
      is_user: true,
    });
    expect(first.revision).toBe(3);
    expect(first.rng_state).not.toBe(8042);
    expect(replay.picks.map((pick) => pick.player_key)).toEqual(
      first.picks.map((pick) => pick.player_key),
    );
  });

  it("records Brian's 1.04 pick and advances across round 1 to his round-2 turn", () => {
    const started = startMock(board, { user_slot: 4, seed: 8042 }, seededMarketStrategy);
    const selected = buildPlayerPool(board.players, started.picks).available[0]!;

    const transition = recordUserPick(started, board, selected.key, seededMarketStrategy);

    expect(transition.appended_picks[0]).toMatchObject({
      overall_pick: 4,
      round: 1,
      round_pick: 4,
      draft_slot: 3,
      team_name: "Brian",
      player_key: selected.key,
      source: "user",
    });
    expect(transition.appended_picks.at(-1)).toMatchObject({
      overall_pick: 20,
      round: 2,
      round_pick: 8,
      source: "simulated",
    });
    expect(transition.next).toMatchObject({
      overall_pick: 21,
      round: 2,
      round_pick: 9,
      team_name: "Brian",
      is_user: true,
      direction: "reverse",
    });
    expect(transition.complete).toBe(false);
  });

  it("rejects a fallback row equivalent to a player already picked by a CPU", () => {
    const canonical = {
      ...board.players[0],
      key: "canonical:alpha",
      name: "Alpha Runner",
      pos: "RB",
      team: "SF",
      rank: 1,
      adp: 1,
    };
    const fallback = {
      ...canonical,
      key: "sleeper:alpha",
      team: "SFO",
      rank: 2,
      adp: 2,
    };
    const identityBoard = {
      ...board,
      num_teams: 2,
      roster_slots: { RB: 1 },
      players: [canonical, fallback, { ...board.players[1], key: "other", rank: 3, adp: 3 }],
    };
    const chooseFirst: OpponentStrategy = {
      version: "test-first",
      choose(context) {
        return { player: context.available[0]!, next_rng_state: context.rng_state };
      },
    };
    const started = startMock(identityBoard, { user_slot: 2, seed: 1 }, chooseFirst);

    expect(started.picks.map((pick) => pick.player_key)).toEqual(["canonical:alpha"]);
    expect(() => recordUserPick(started, identityBoard, "sleeper:alpha", chooseFirst)).toThrowError(
      expect.objectContaining({ code: "player_unavailable" }),
    );
  });

  it("varies the bounded market sequence with a different seed", () => {
    const first = startMock(board, { user_slot: 4, seed: 8042 }, seededMarketStrategy);
    const varied = startMock(board, { user_slot: 4, seed: 987654321 }, seededMarketStrategy);

    expect(varied.picks.map((pick) => pick.player_key)).not.toEqual(
      first.picks.map((pick) => pick.player_key),
    );
  });

  it("fails a transition without returning partial picks when the pool is exhausted", () => {
    const thinBoard = {
      ...board,
      num_teams: 4,
      roster_slots: { RB: 1 },
      players: board.players.slice(0, 2),
    };

    expect(() =>
      startMock(thinBoard, { user_slot: 4, seed: 8042 }, seededMarketStrategy),
    ).toThrowError(expect.objectContaining({ code: "board_unusable" }));
  });
});
