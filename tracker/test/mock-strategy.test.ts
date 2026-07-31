import { describe, expect, it } from "vitest";
import {
  marketNeedStrategy,
  normalizeInitialRngState,
  strategyForVersion,
  type OpponentContext,
  type VariancePreset,
} from "../src/mock-strategy";
import type { Player } from "../src/types";

function candidate(index: number): Player {
  const positions = ["QB", "RB", "WR", "TE", "DEF"];
  return {
    key: `player-${index}`,
    name: `Player ${String(index).padStart(2, "0")}`,
    pos: positions[index % positions.length]!,
    team: null,
    bye: null,
    points: 100 - index,
    n_sources: 2,
    vorp: 20 - index,
    tier: (index % 4) + 1,
    rank: index + 1,
    pos_rank: index + 1,
    adp: index + 1,
    adp_rank: index + 1,
    adp_high: null,
    adp_low: null,
    adp_stdev: null,
    matched: true,
  };
}

function context(preset: VariancePreset, seed = 8042): OpponentContext {
  return {
    candidates: Array.from({ length: 14 }, (_, index) => candidate(index)),
    team_picks: [],
    roster_slots: { QB: 1, RB: 1, WR: 1, TE: 1, DEF: 1, BN: 3 },
    next: {
      overall_pick: 1, round: 1, round_pick: 1, team_id: 1,
      team_name: "CPU 1", is_user: false, direction: "forward",
    },
    rounds: 8,
    variance_preset: preset,
    rng_state: seed,
  };
}

describe("market-need-v1 opponent strategy", () => {
  it.each([
    ["calm", 4],
    ["realistic", 8],
    ["wild", 12],
  ] as const)("confines %s choices and random draws to its market window", (preset, window) => {
    const choice = marketNeedStrategy.choose(context(preset));

    expect(Number(choice.player_key.slice("player-".length))).toBeLessThan(window);
    expect(choice.next_rng_state).not.toBe(8042);
    expect(choice.rationale.total).toBeCloseTo(
      choice.rationale.market
      + choice.rationale.need
      + choice.rationale.tier_value
      + choice.rationale.specialist_penalty
      + choice.rationale.seeded_noise,
    );
  });

  it("replays exactly, including a normalized nonzero state for seed zero", () => {
    const initial = normalizeInitialRngState(0);
    const first = marketNeedStrategy.choose(context("realistic", initial));
    const replay = marketNeedStrategy.choose(context("realistic", initial));

    expect(initial).toBe(0x9e37_79b9);
    expect(replay).toEqual(first);
    expect(first.next_rng_state).not.toBe(0);
  });

  it.each([
    ["calm", 835033615, 0.06248410073014058, 16.46248410073014],
    ["realistic", 3434734187, 0.16662426861370822, 16.566624268613708],
    ["wild", 1714639183, 0.3124205036507029, 16.7124205036507],
  ] as const)("freezes the exact %s score and candidate draw sequence", (
    preset,
    expectedState,
    expectedNoise,
    expectedTotal,
  ) => {
    const choice = marketNeedStrategy.choose(context(preset, 8042));
    expect(choice).toMatchObject({
      player_key: "player-0",
      next_rng_state: expectedState,
      rationale: {
        market: 12,
        need: 3,
        tier_value: 1.4,
        specialist_penalty: 0,
        seeded_noise: expectedNoise,
        total: expectedTotal,
      },
    });
  });

  it("uses ADP presence, ADP rank, board rank, name, and key as stable market fallbacks", () => {
    const tied = candidate(0);
    const candidates = [
      { ...candidate(3), key: "no-adp-rank", adp: null, adp_rank: 1, rank: 1 },
      { ...candidate(2), key: "board-rank", adp: null, adp_rank: null, rank: 1 },
      { ...tied, key: "z-key", name: "Same", adp: 1, adp_rank: 1, rank: 1 },
      { ...tied, key: "a-key", name: "Same", adp: 1, adp_rank: 1, rank: 1 },
    ];
    const choice = marketNeedStrategy.choose({
      ...context("calm", 8042),
      candidates,
      roster_slots: { QB: 1, BN: 3 },
    });
    expect(choice.player_key).toBe("a-key");
  });

  it("penalizes specialists before, but not during, the final four rounds", () => {
    const defense = { ...candidate(0), key: "defense", pos: "DEF", tier: 3 };
    const early = marketNeedStrategy.choose({
      ...context("calm"), candidates: [defense], roster_slots: { DEF: 1 },
      next: { ...context("calm").next, round: 4 },
    });
    const late = marketNeedStrategy.choose({
      ...context("calm"), candidates: [defense], roster_slots: { DEF: 1 },
      next: { ...context("calm").next, round: 5 },
    });
    expect(early.rationale.specialist_penalty).toBe(-5);
    expect(late.rationale.specialist_penalty).toBe(0);
    expect(late.rationale.need).toBeGreaterThan(early.rationale.need);
  });

  it("keeps both saved strategy versions addressable and rejects unknown versions", () => {
    expect(strategyForVersion("seeded-market-v0")?.version).toBe("seeded-market-v0");
    expect(strategyForVersion("market-need-v1")?.version).toBe("market-need-v1");
    expect(strategyForVersion("future-v2")).toBeNull();
  });
});
