import { describe, expect, it } from "vitest";
import { isCompleteRoster, rosterFit, safePositionsForTurn } from "../src/roster-fit";
import type { MockPick } from "../src/mock-draft";
import type { Player } from "../src/types";

const player = (key: string, pos: string | null): Player => ({
  key, name: key, pos, team: null, bye: null, points: 1, n_sources: 1,
  vorp: 1, tier: 1, rank: 1, pos_rank: 1, adp: 1, adp_rank: 1,
  adp_high: null, adp_low: null, adp_stdev: null, matched: true,
});

const pick = (draftSlot: number, pos: string): MockPick => ({
  overall_pick: draftSlot + 1, round: 1, round_pick: draftSlot + 1,
  team_name: `Team ${draftSlot}`, draft_slot: draftSlot, player_key: `pick-${draftSlot}`,
  player_name: `Pick ${draftSlot}`, player_pos: pos, player_team: null, source: "simulated",
});

describe("roster fit", () => {
  it("fits a second quarterback on the bench after filling the starter slot", () => {
    expect(rosterFit({ QB: 1, BN: 1 }, ["QB", "QB"])).toEqual({
      legal: true,
      filledStarters: 1,
      openStarters: 0,
    });
  });

  it("fits a wide receiver into a W/T starter slot", () => {
    expect(rosterFit({ "W/T": 1 }, ["WR"]).legal).toBe(true);
  });

  it("uses exact matching for repeated Yahoo flex slots regardless of pick order", () => {
    const slots = { TE: 1, "W/T": 1, "W/R/T": 2 };
    expect(rosterFit(slots, ["TE", "WR", "RB", "RB"]).legal).toBe(true);
    expect(rosterFit(slots, ["RB", "TE", "RB", "WR"]).legal).toBe(true);
    expect(rosterFit({ "W/T": 1 }, ["RB"]).legal).toBe(false);
  });

  it("only reports a complete roster when every configured slot is legally filled", () => {
    const yahoo = { QB: 1, WR: 1, RB: 1, TE: 1, "W/T": 1, "W/R/T": 2, DEF: 1, BN: 8 };
    const legal = ["QB", "QB", "WR", "WR", "WR", "RB", "RB", "RB", "TE", "TE", "DEF", "WR", "RB", "TE", "WR", "RB"];
    expect(isCompleteRoster(yahoo, legal)).toBe(true);
    expect(isCompleteRoster(yahoo, legal.map((position) => position === "DEF" ? "QB" : position))).toBe(false);
  });

  it("prevents one team from benching the final defense another team needs", () => {
    const safe = safePositionsForTurn({
      rosterSlots: { DEF: 1, BN: 1 },
      teamCount: 2,
      picks: [pick(0, "DEF"), pick(1, "QB")],
      available: [player("last-defense", "DEF"), player("bench-quarterback", "QB")],
      draftSlot: 0,
    });

    expect([...safe]).toEqual(["QB"]);
  });

  it("supports every dedicated position and rejects unsupported positive slot labels", () => {
    for (const position of ["QB", "RB", "WR", "TE", "K", "DEF"]) {
      expect(rosterFit({ [position]: 1 }, [position]).legal).toBe(true);
    }
    expect(rosterFit({ SUPERFLEX: 1 }, ["QB"]).legal).toBe(false);
    expect(rosterFit({ QB: -1 }, []).legal).toBe(false);
  });

  it("accounts for combined flex supply across teams", () => {
    const flexible = safePositionsForTurn({
      rosterSlots: { "W/R/T": 1 },
      teamCount: 2,
      picks: [],
      available: [player("runner", "RB"), player("receiver", "WR")],
      draftSlot: 0,
    });
    expect([...flexible]).toEqual(["RB", "WR"]);

    expect(safePositionsForTurn({
      rosterSlots: { "W/R/T": 1 },
      teamCount: 2,
      picks: [],
      available: [player("only-runner", "RB")],
      draftSlot: 0,
    }).size).toBe(0);
  });

  it("rejects picks beyond bench capacity and ignores null or unknown positions", () => {
    expect(safePositionsForTurn({
      rosterSlots: { QB: 1 },
      teamCount: 1,
      picks: [pick(0, "QB")],
      available: [player("second-qb", "QB")],
      draftSlot: 0,
    }).size).toBe(0);
    expect(safePositionsForTurn({
      rosterSlots: { QB: 1 },
      teamCount: 1,
      picks: [],
      available: [player("null", null), player("unknown", "UNKNOWN")],
      draftSlot: 0,
    }).size).toBe(0);
  });

  it("detects an initially infeasible positional pool despite enough raw rows", () => {
    expect(safePositionsForTurn({
      rosterSlots: { QB: 1, DEF: 1 },
      teamCount: 2,
      picks: [],
      available: [
        player("qb-1", "QB"), player("qb-2", "QB"),
        player("qb-3", "QB"), player("qb-4", "QB"),
      ],
      draftSlot: 0,
    }).size).toBe(0);
  });

  it("does not count canonical and fallback aliases as separate future supply", () => {
    const canonical = { ...player("canonical:runner", "RB"), name: "Alias Runner", team: "SF" };
    const fallback = { ...player("sleeper:runner", "RB"), name: "Alias Runner", team: "SFO" };
    expect(safePositionsForTurn({
      rosterSlots: { RB: 1 },
      teamCount: 2,
      picks: [],
      available: [canonical, fallback],
      draftSlot: 0,
    }).size).toBe(0);
  });
});
