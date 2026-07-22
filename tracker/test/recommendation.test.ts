import { describe, expect, it } from "vitest";
import { deriveRecommendation, rosterNeeds } from "../src/recommendation";
import type { Board, Player } from "../src/types";
import type { DraftState } from "../src/draft-store";

describe("roster needs", () => {
  it("fills dedicated starters before FLEX and leaves bench slots out of positional deficits", () => {
    const needs = rosterNeeds(
      { QB: 1, RB: 2, WR: 1, "W/R/T": 1, BN: 5 },
      [
        { player_pos: "RB" },
        { player_pos: "RB" },
        { player_pos: "RB" },
        { player_pos: "WR" },
        { player_pos: null },
      ],
    );

    expect(needs).toMatchObject({
      picked: { RB: 3, WR: 1 },
      openDedicated: { QB: 1, RB: 0, WR: 0 },
      openFlex: 0,
      openStarters: 1,
      benchPicks: 1,
      unknownPicks: 1,
      unsupportedOpen: {},
    });
  });
});

describe("your-pick recommendation", () => {
  const players = [
    { key: "rb", name: "Best RB", pos: "RB", team: "BUF", vorp: 100, tier: 1, rank: 1, adp: 1 },
    { key: "qb", name: "Needed QB", pos: "QB", team: "KC", vorp: 50, tier: 1, rank: 2, adp: 2 },
    { key: "k", name: "Kicker", pos: "K", team: "KC", vorp: null, tier: null, rank: 3, adp: null },
  ].map((player) => ({ bye: null, points: null, n_sources: 1, pos_rank: 1, adp_rank: null, adp_high: null, adp_low: null, adp_stdev: null, matched: true, ...player })) as Player[];
  const board: Board = { version: 1, season: 2026, generated_at: "now", scoring: "PPR", num_teams: 2, roster_slots: { QB: 1, RB: 1, K: 1, BN: 5 }, players };
  const state = {
    configured: true,
    draft: { name: "Draft", rounds: 4, team_count: 2 },
    teams: [
      { id: 1, name: "Brian", draft_slot: 0, is_user: true },
      { id: 2, name: "Other", draft_slot: 1, is_user: false },
    ],
    picks: [],
    next: { overall_pick: 1, round: 1, round_pick: 1, team_id: 1, team_name: "Brian", is_user: true, direction: "forward" },
    complete: false,
    revision: 0,
  } as DraftState;

  it("prioritizes an open dedicated starter while delaying K/DEF", () => {
    const result = deriveRecommendation(board, {
      ...state,
      picks: [{ overall_pick: 1, round: 1, round_pick: 1, team_id: 1, team_name: "Brian", player_key: "taken-rb", player_name: "Taken RB", player_pos: "RB", player_team: "BUF", picked_at: "now" }],
      next: { ...state.next!, overall_pick: 3, round: 2, round_pick: 1 },
    });
    expect(result.recommendation).toMatchObject({ player: { key: "qb" }, need: "dedicated", position: "QB" });
    expect(result.context).toMatchObject({ picksUntilNextTurn: 0, remainingUserPicks: 3 });
  });

  it("keeps context but hides the Brian-specific recommendation on an opponent turn", () => {
    const result = deriveRecommendation(board, { ...state, next: { ...state.next!, overall_pick: 2, team_id: 2, team_name: "Other", is_user: false } });
    expect(result.context).not.toBeNull();
    expect(result.recommendation).toBeNull();
  });

  it("fails closed instead of throwing when called with malformed board data", () => {
    const result = deriveRecommendation({ version: 1, players: [] } as unknown as Board, state);
    expect(result).toMatchObject({ context: null, recommendation: null });
    expect(result.warnings.join(" ")).toContain("malformed");
  });
});
