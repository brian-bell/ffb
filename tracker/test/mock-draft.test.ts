import { describe, expect, it } from "vitest";
import { recordUserPick, startMock, type MockAggregate } from "../src/mock-draft";
import { buildPlayerPool } from "../src/player-pool";
import {
  marketNeedStrategy,
  seededMarketStrategy,
  type OpponentStrategy,
  type VariancePreset,
} from "../src/mock-strategy";
import { isCompleteRoster, safePositionsForTurn } from "../src/roster-fit";
import type { Board } from "../src/types";
import fixtureJson from "./fixtures/board.json";

const board = {
  ...fixtureJson,
  players: [
    ...fixtureJson.players,
    ...Array.from({ length: 12 * 15 - fixtureJson.players.length }, (_, index) => ({
      ...fixtureJson.players[0],
      key: `extra-${index}`,
      name: `Extra Player ${index}`,
      pos: ["QB", "RB", "WR", "TE", "K", "DEF"][index % 6],
      team: `T${index}`,
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

const yahooRoster = { QB: 1, WR: 1, RB: 1, TE: 1, "W/T": 1, "W/R/T": 2, DEF: 1, BN: 8 };
const yahooPositions = [
  ...Array(25).fill("QB"),
  ...Array(45).fill("RB"),
  ...Array(45).fill("WR"),
  ...Array(25).fill("TE"),
  ...Array(20).fill("DEF"),
] as string[];
const yahooBoard = {
  ...board,
  num_teams: 10,
  roster_slots: yahooRoster,
  scoring: "Yahoo half PPR",
  players: yahooPositions.map((pos, index) => ({
    ...board.players[index % board.players.length]!,
    key: `yahoo-${index}`,
    name: `Yahoo ${pos} ${index}`,
    pos,
    team: pos === "DEF" ? `D${index}` : `T${index}`,
    rank: index + 1,
    pos_rank: yahooPositions.slice(0, index + 1).filter((value) => value === pos).length,
    adp: index + 1,
    adp_rank: index + 1,
    tier: Math.floor(index / 20) + 1,
  })),
} as Board;

const yahooSimulationBatches = (["calm", "realistic", "wild"] as const).flatMap(
  (preset) => [0, 10, 20, 30].map(
    (seedStart) => [preset, seedStart, seedStart + 10] as const,
  ),
);

function completeYahooMock(preset: VariancePreset, seed: number, userSlot: number): MockAggregate {
  let aggregate = startMock(
    yahooBoard,
    { user_slot: userSlot, seed, variance_preset: preset },
    marketNeedStrategy,
  );
  while (!aggregate.complete) {
    const available = buildPlayerPool(yahooBoard.players, aggregate.picks).available;
    const safe = safePositionsForTurn({
      rosterSlots: yahooRoster,
      teamCount: 10,
      picks: aggregate.picks,
      available,
      draftSlot: userSlot - 1,
    });
    const selected = available.find((player) => player.pos && safe.has(player.pos));
    if (!selected) throw new Error("no safe user choice");
    const transition = recordUserPick(aggregate, yahooBoard, selected.key, marketNeedStrategy);
    aggregate = {
      ...aggregate,
      picks: [...aggregate.picks, ...transition.appended_picks],
      next: transition.next,
      complete: transition.complete,
      revision: aggregate.revision + transition.appended_picks.length,
      rng_state: transition.next_rng_state,
    };
  }
  return aggregate;
}

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

    expect(transition.checkpoint).toEqual({
      decision_overall_pick: 4,
      pick_count_before: 3,
      rng_state_before: started.rng_state,
    });
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
      players: [
        canonical,
        fallback,
        { ...board.players[1], key: "other", pos: "RB", team: "OTHER", rank: 3, adp: 3 },
      ],
    };
    const chooseFirst: OpponentStrategy = {
      version: "test-first",
      choose(context) {
        return {
          player_key: context.candidates[0]!.key,
          next_rng_state: context.rng_state,
          rationale: {
            market: 0, need: 0, tier_value: 0, specialist_penalty: 0,
            seeded_noise: 0, total: 0,
          },
        };
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

  it("rejects a board without enough distinct players to complete every pick", () => {
    const duplicate = { ...board.players[0], key: "sleeper:duplicate", rank: 2 };
    const thinBoard = {
      ...board,
      num_teams: 2,
      roster_slots: { RB: 2 },
      players: [board.players[0], duplicate, board.players[1], board.players[2]],
    };

    expect(() =>
      startMock(thinBoard, { user_slot: 1, seed: 8042 }, unusedStrategy),
    ).toThrowError(expect.objectContaining({ code: "board_unusable" }));
  });

  it("rejects a user pick that consumes the final defense another roster needs", () => {
    const players = [
      { ...board.players[0]!, key: "owned-def", name: "Owned DEF", pos: "DEF", team: "ONE" },
      { ...board.players[1]!, key: "owned-qb", name: "Owned QB", pos: "QB", team: "TWO" },
      { ...board.players[2]!, key: "last-def", name: "Last DEF", pos: "DEF", team: "THREE" },
      { ...board.players[3]!, key: "last-qb", name: "Last QB", pos: "QB", team: "FOUR" },
    ];
    const scarceBoard = { ...board, num_teams: 2, roster_slots: { DEF: 1, BN: 1 }, players };
    const aggregate: MockAggregate = {
      seed: 1,
      strategy_version: seededMarketStrategy.version,
      variance_preset: "realistic",
      user_slot: 1,
      team_count: 2,
      rounds: 2,
      teams: [
        { id: 1, name: "Brian", draft_slot: 0, is_user: true },
        { id: 2, name: "CPU 2", draft_slot: 1, is_user: false },
      ],
      picks: [
        { overall_pick: 1, round: 1, round_pick: 1, team_name: "Brian", draft_slot: 0,
          player_key: "owned-def", player_name: "Owned DEF", player_pos: "DEF", player_team: "ONE", source: "user" },
        { overall_pick: 2, round: 1, round_pick: 2, team_name: "CPU 2", draft_slot: 1,
          player_key: "owned-qb", player_name: "Owned QB", player_pos: "QB", player_team: "TWO", source: "simulated" },
      ],
      next: { overall_pick: 3, round: 2, round_pick: 1, team_id: 1, team_name: "Brian", is_user: true, direction: "reverse" },
      complete: false,
      revision: 2,
      rng_state: 1,
    };

    expect(() => recordUserPick(aggregate, scarceBoard, "last-def", seededMarketStrategy))
      .toThrowError(expect.objectContaining({ code: "illegal_roster_pick" }));
    expect(aggregate.picks).toHaveLength(2);
    expect(aggregate.rng_state).toBe(1);
  });

  it.each(yahooSimulationBatches)(
    "completes 10 legal %s Yahoo-shaped drafts for seeds %i-%i",
    (preset, seedStart, seedEnd) => {
      let representative: string[] | null = null;
      for (let seed = seedStart; seed < seedEnd; seed += 1) {
        const userSlot = [1, 5, 10][seed % 3]!;
        const completed = completeYahooMock(preset, seed, userSlot);
        expect(completed.picks).toHaveLength(160);
        expect(new Set(completed.picks.map((pick) => pick.player_key)).size).toBe(160);
        expect(completed.picks.every((pick) => pick.player_pos !== "K")).toBe(true);
        completed.picks.forEach((pick, index) => {
          const round = Math.floor(index / 10) + 1;
          const offset = index % 10;
          expect(pick.draft_slot).toBe(round % 2 === 1 ? offset : 9 - offset);
        });
        for (let slot = 0; slot < 10; slot += 1) {
          expect(isCompleteRoster(
            yahooRoster,
            completed.picks.filter((pick) => pick.draft_slot === slot)
              .map((pick) => pick.player_pos)
              .filter((pos): pos is string => Boolean(pos)),
          )).toBe(true);
        }
        if (seed === 0) representative = completed.picks.map((pick) => pick.player_key);
      }
      if (seedStart === 0) {
        expect(completeYahooMock(preset, 0, 1).picks.map((pick) => pick.player_key))
          .toEqual(representative);
      }
    },
    120_000,
  );
});
