import { indexPlayerIdentities, type PlayerIdentity } from "./player-identity";
import { buildPlayerSearch } from "./suggestions";
import type { PickAnnotation } from "./render";
import type { Player } from "./types";

export interface PlayerPoolPick {
  overall_pick: number;
  round: number;
  round_pick: number;
  team_name: string;
  player_key: string;
  player_name: string;
  player_pos: string | null;
  player_team: string | null;
}

export interface PlayerPool {
  readonly picked: ReadonlyMap<string, PickAnnotation>;
  search(query: string): Player[];
}

export function buildPlayerPool(players: Player[], picks: readonly PlayerPoolPick[]): PlayerPool {
  const indexedPicks = picks.map((pick) => ({
    ...pick,
    key: pick.player_key,
    name: pick.player_name,
    pos: pick.player_pos,
    team: pick.player_team,
  })) satisfies Array<PlayerPoolPick & PlayerIdentity>;
  const pickIndex = indexPlayerIdentities(indexedPicks);
  const picked = new Map<string, PickAnnotation>();
  const available: Player[] = [];
  for (const player of players) {
    const pick = pickIndex.match(player);
    if (pick) {
      picked.set(player.key, {
        overall_pick: pick.overall_pick,
        round: pick.round,
        round_pick: pick.round_pick,
        team_name: pick.team_name,
      });
    } else {
      available.push(player);
    }
  }
  const playerSearch = buildPlayerSearch(available);
  return {
    picked,
    search: (query) => playerSearch.search(query),
  };
}
