import { indexPlayerIdentities, type PlayerIdentity } from "./player-identity";
import type { Player } from "./types";

export function marketOrder(a: Player, b: Player): number {
  const aHasAdp = typeof a.adp === "number";
  const bHasAdp = typeof b.adp === "number";
  if (aHasAdp !== bHasAdp) return aHasAdp ? -1 : 1;
  if (aHasAdp && bHasAdp && a.adp !== b.adp) return a.adp! - b.adp!;
  const aAdpRank = a.adp_rank ?? Number.POSITIVE_INFINITY;
  const bAdpRank = b.adp_rank ?? Number.POSITIVE_INFINITY;
  if (aAdpRank !== bAdpRank) return aAdpRank - bAdpRank;
  if (a.rank !== b.rank) return a.rank - b.rank;
  const byName = a.name.localeCompare(b.name);
  return byName !== 0 ? byName : a.key.localeCompare(b.key);
}

type Picked = readonly PlayerIdentity[] | ReadonlySet<string>;

function snapshots(picked: Picked): readonly PlayerIdentity[] {
  if ("has" in picked) return [...(picked as ReadonlySet<string>)].map((key) => ({ key, name: "", pos: null, team: null }));
  return picked as readonly PlayerIdentity[];
}

export function availablePlayers(players: readonly Player[], picked: Picked): Player[] {
  const pickedIndex = indexPlayerIdentities(snapshots(picked));
  return players.filter((player) => pickedIndex.match(player) === undefined);
}

/** Rank an already identity-filtered pool without mutating its source array. */
export function suggestAvailablePlayers(
  available: readonly Player[],
  limit = 3,
): Player[] {
  return [...available].sort(marketOrder).slice(0, Math.max(0, limit));
}

/** Compatibility wrapper for callers that have not yet built a PlayerPool. */
export function suggestPlayers(players: readonly Player[], picked: Picked): Player[] {
  return suggestAvailablePlayers(availablePlayers(players, picked));
}

function normalized(value: string): string {
  return value.toLocaleLowerCase().replace(/[^\p{L}\p{N}]+/gu, "");
}

function tokens(value: string): string[] {
  return value.toLocaleLowerCase().split(/[^\p{L}\p{N}]+/gu).filter(Boolean);
}

export interface PlayerSearch {
  search(query: string): Player[];
}

/** Cache immutable player-name normalization and market order for repeated queries. */
export function buildPlayerSearch(players: readonly Player[]): PlayerSearch {
  const entries = players
    .map((player) => ({
      player,
      name: normalized(player.name),
      words: tokens(player.name).map(normalized),
    }))
    .sort((a, b) => marketOrder(a.player, b.player));

  return {
    search(query) {
      const needle = normalized(query.trim());
      if (!needle) return [];
      const matches: [Player[], Player[], Player[]] = [[], [], []];
      for (const entry of entries) {
        const match = entry.name.startsWith(needle)
          ? 0
          : entry.words.some((word) => word.startsWith(needle))
            ? 1
            : entry.name.includes(needle)
              ? 2
              : 3;
        const bucket = match === 0 ? matches[0] : match === 1 ? matches[1] : match === 2 ? matches[2] : null;
        if (bucket && bucket.length < 8) bucket.push(entry.player);
      }
      return matches.flat().slice(0, 8);
    },
  };
}

/** Search exact board rows only; callers must submit the selected row's key. */
export function searchPlayers(players: readonly Player[], picked: Picked, query: string): Player[] {
  return buildPlayerSearch(availablePlayers(players, picked)).search(query);
}
