import type { NextPick } from "./draft";
import type { MockPick } from "./mock-draft";
import { rosterFit } from "./roster-fit";
import type { Player } from "./types";

export type VariancePreset = "calm" | "realistic" | "wild";

export interface OpponentContext {
  candidates: readonly Player[];
  team_picks: readonly MockPick[];
  roster_slots: Readonly<Record<string, number>>;
  next: NextPick;
  rounds: number;
  variance_preset: VariancePreset;
  rng_state: number;
}

export interface OpponentRationale {
  market: number;
  need: number;
  tier_value: number;
  specialist_penalty: number;
  seeded_noise: number;
  total: number;
}

export interface OpponentChoice {
  player_key: string;
  next_rng_state: number;
  rationale: OpponentRationale;
}

export interface OpponentStrategy {
  readonly version: string;
  choose(context: OpponentContext): OpponentChoice;
}

const PRESETS: Readonly<Record<VariancePreset, { window: number; temperature: number }>> = {
  calm: { window: 4, temperature: 0.18 },
  realistic: { window: 8, temperature: 0.48 },
  wild: { window: 12, temperature: 0.90 },
};

function xorshift32(state: number): number {
  let next = state >>> 0;
  next ^= next << 13;
  next ^= next >>> 17;
  next ^= next << 5;
  return next >>> 0;
}

export function normalizeInitialRngState(seed: number): number {
  return seed === 0 ? 0x9e37_79b9 : seed >>> 0;
}

function stableMarketOrder(a: Player, b: Player): number {
  const aHasAdp = typeof a.adp === "number";
  const bHasAdp = typeof b.adp === "number";
  if (aHasAdp !== bHasAdp) return aHasAdp ? -1 : 1;
  if (aHasAdp && bHasAdp && a.adp !== b.adp) return a.adp! - b.adp!;
  const adpRank = (a.adp_rank ?? Number.POSITIVE_INFINITY)
    - (b.adp_rank ?? Number.POSITIVE_INFINITY);
  if (adpRank !== 0) return adpRank;
  if (a.rank !== b.rank) return a.rank - b.rank;
  if (a.name !== b.name) return a.name < b.name ? -1 : 1;
  return a.key === b.key ? 0 : a.key < b.key ? -1 : 1;
}

function emptyRationale(): OpponentRationale {
  return {
    market: 0,
    need: 0,
    tier_value: 0,
    specialist_penalty: 0,
    seeded_noise: 0,
    total: 0,
  };
}

export const seededMarketStrategy: OpponentStrategy = {
  version: "seeded-market-v0",
  choose(context) {
    const candidates = [...context.candidates].sort(stableMarketOrder).slice(0, 4);
    if (candidates.length === 0) throw new Error("no_available_players");
    const nextRngState = xorshift32(context.rng_state);
    const index = Math.floor((nextRngState / 0x1_0000_0000) * candidates.length);
    return {
      player_key: candidates[index]!.key,
      next_rng_state: nextRngState,
      rationale: emptyRationale(),
    };
  },
};

export const marketNeedStrategy: OpponentStrategy = {
  version: "market-need-v1",
  choose(context) {
    const preset = PRESETS[context.variance_preset];
    if (!preset) throw new Error("invalid_variance_preset");
    const candidates = [...context.candidates]
      .sort(stableMarketOrder)
      .slice(0, preset.window);
    if (candidates.length === 0) throw new Error("no_available_players");

    const positions = context.team_picks
      .map((pick) => pick.player_pos)
      .filter((position): position is string => typeof position === "string");
    const before = rosterFit(context.roster_slots, positions);
    const roundProgress = context.rounds <= 1
      ? 1
      : (context.next.round - 1) / (context.rounds - 1);
    let rngState = context.rng_state;
    let best: OpponentChoice | null = null;

    candidates.forEach((candidate, windowIndex) => {
      rngState = xorshift32(rngState);
      const unit = (rngState + 0.5) / 0x1_0000_0000;
      const market = Math.max(0, 12 - windowIndex * 1.1);
      const after = candidate.pos
        ? rosterFit(context.roster_slots, [...positions, candidate.pos])
        : before;
      const need = candidate.pos && after.legal && after.filledStarters > before.filledStarters
        ? 3 + 5 * roundProgress
        : 0;
      const tierValue = Math.max(0, 3 - (candidate.tier ?? 3)) * 0.7;
      const specialistPenalty = (candidate.pos === "K" || candidate.pos === "DEF")
        && context.next.round <= context.rounds - 4
        ? -5
        : 0;
      const seededNoise = -Math.log(-Math.log(unit)) * preset.temperature;
      const total = market + need + tierValue + specialistPenalty + seededNoise;
      const choice: OpponentChoice = {
        player_key: candidate.key,
        next_rng_state: 0,
        rationale: {
          market,
          need,
          tier_value: tierValue,
          specialist_penalty: specialistPenalty,
          seeded_noise: seededNoise,
          total,
        },
      };
      if (!best || total > best.rationale.total) best = choice;
    });

    return { ...best!, next_rng_state: rngState };
  },
};

export function strategyForVersion(version: string): OpponentStrategy | null {
  if (version === seededMarketStrategy.version) return seededMarketStrategy;
  if (version === marketNeedStrategy.version) return marketNeedStrategy;
  return null;
}

export function isVariancePreset(value: unknown): value is VariancePreset {
  return typeof value === "string" && Object.hasOwn(PRESETS, value);
}
