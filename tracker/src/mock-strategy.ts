import type { NextPick } from "./draft";
import type { MockPick } from "./mock-draft";
import { marketOrder } from "./suggestions";
import type { Player } from "./types";

export interface OpponentContext {
  available: readonly Player[];
  next: NextPick;
  picks: readonly MockPick[];
  rng_state: number;
}

export interface OpponentChoice {
  player: Player;
  next_rng_state: number;
}

export interface OpponentStrategy {
  readonly version: string;
  choose(context: OpponentContext): OpponentChoice;
}

function xorshift32(state: number): number {
  let next = state >>> 0;
  next ^= next << 13;
  next ^= next >>> 17;
  next ^= next << 5;
  return next >>> 0;
}

export const seededMarketStrategy: OpponentStrategy = {
  version: "seeded-market-v0",
  choose(context) {
    const candidates = [...context.available].sort(marketOrder).slice(0, 4);
    if (candidates.length === 0) {
      throw new Error("no_available_players");
    }
    const nextRngState = xorshift32(context.rng_state);
    const index = Math.floor((nextRngState / 0x1_0000_0000) * candidates.length);
    return {
      player: candidates[index]!,
      next_rng_state: nextRngState,
    };
  },
};
