import { isAvailable, type PlayerIdentity } from "./player-identity";
import type { Player } from "./types";

function marketOrder(a: Player, b: Player): number {
  const aHasAdp = typeof a.adp === "number";
  const bHasAdp = typeof b.adp === "number";
  if (aHasAdp !== bHasAdp) return aHasAdp ? -1 : 1;
  if (aHasAdp && bHasAdp && a.adp !== b.adp) return a.adp! - b.adp!;
  const aAdpRank = a.adp_rank ?? Number.POSITIVE_INFINITY;
  const bAdpRank = b.adp_rank ?? Number.POSITIVE_INFINITY;
  if (aAdpRank !== bAdpRank) return aAdpRank - bAdpRank;
  if (a.rank !== b.rank) return a.rank - b.rank;
  return a.name.localeCompare(b.name);
}

type Picked = readonly PlayerIdentity[] | ReadonlySet<string>;

function snapshots(picked: Picked): readonly PlayerIdentity[] {
  if ("has" in picked) return [...(picked as ReadonlySet<string>)].map((key) => ({ key, name: "", pos: null, team: null }));
  return picked as readonly PlayerIdentity[];
}

export function availablePlayers(players: Player[], picked: Picked): Player[] {
  return players.filter((player) => isAvailable(player, snapshots(picked)));
}

/** Three available players most likely to be selected next by market ADP. */
export function suggestPlayers(players: Player[], picked: Picked): Player[] {
  return availablePlayers(players, picked).sort(marketOrder).slice(0, 3);
}

function normalized(value: string): string {
  return value.toLocaleLowerCase().replace(/[^\p{L}\p{N}]+/gu, "");
}

function tokens(value: string): string[] {
  return value.toLocaleLowerCase().split(/[^\p{L}\p{N}]+/gu).filter(Boolean);
}

/** Search exact board rows only; callers must submit the selected row's key. */
export function searchPlayers(players: Player[], picked: Picked, query: string): Player[] {
  const needle = normalized(query.trim());
  if (!needle) return [];
  return availablePlayers(players, picked)
    .map((player) => {
      const name = normalized(player.name);
      const match = name.startsWith(needle) ? 0 : tokens(player.name).some((word) => word.startsWith(needle)) ? 1 : name.includes(needle) ? 2 : 3;
      return { player, match };
    })
    .filter(({ match }) => match < 3)
    .sort((a, b) => a.match - b.match || marketOrder(a.player, b.player))
    .slice(0, 8)
    .map(({ player }) => player);
}
