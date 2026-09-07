import type { DraftState } from "./draft-store";
import type { Board } from "./types";
import { distinctPlayerIdentities, indexPlayerIdentities, normalizedPosition } from "./player-identity";

// Brian's requested live-draft lineup. Independent of saved mock league shapes.
const STARTERS = { QB: 1, RB: 2, WR: 1, TE: 1, DEF: 1 } as const;
const FLEX_POSITIONS = ["RB", "WR", "TE"];

export interface StarterPriority {
  positions: ReadonlySet<string>;
  summary: string;
  byeConflicts: ReadonlyMap<string, number>;
}

export function liveStarterPriority(draft: DraftState | null, board?: Board): StarterPriority | null {
  const user = draft?.configured ? draft.teams?.find(team => team.is_user) : undefined;
  if (!user || !draft) return null;
  const players = distinctPlayerIdentities(draft.picks
    .filter(pick => pick.team_id === user.id)
    .map(pick => ({ key: pick.player_key, name: pick.player_name, pos: pick.player_pos, team: pick.player_team })));
  const counts = new Map<string, number>();
  for (const player of players) {
    const pos = normalizedPosition(player.pos);
    if (pos) counts.set(pos, (counts.get(pos) ?? 0) + 1);
  }
  const remaining = new Map(Object.entries(STARTERS)
    .map(([pos, slots]) => [pos, Math.max(0, slots - (counts.get(pos) ?? 0))] as const));
  // Reserve surplus WR/TE for the narrower slot before filling the broad FLEX.
  const surplus = new Map(Object.entries(STARTERS)
    .map(([pos, slots]) => [pos, Math.max(0, (counts.get(pos) ?? 0) - slots)] as const));
  const receiverSurplus = surplus.get("WR")! + surplus.get("TE")!;
  const receiverFlexFilled = receiverSurplus > 0;
  const flexFilled = surplus.get("RB")! + receiverSurplus - Number(receiverFlexFilled) > 0;
  const positions = new Set(Object.keys(STARTERS).filter(pos =>
    remaining.get(pos)! > 0
    || (!receiverFlexFilled && (pos === "WR" || pos === "TE"))
    || (!flexFilled && FLEX_POSITIONS.includes(pos))));
  const needs = ["QB", "RB", "WR", "TE", "WR/TE", "FLEX", "DEF"].flatMap(pos => {
    const count = pos === "WR/TE" ? Number(!receiverFlexFilled)
      : pos === "FLEX" ? Number(!flexFilled) : remaining.get(pos)!;
    return count > 0 ? [`${pos} ${count}`] : [];
  });
  const ownedByes = new Map<string, number>();
  for (const player of players) {
    const index = indexPlayerIdentities([player], board?.players ?? []);
    const match = board?.players.find(candidate => candidate.key === player.key)
      ?? board?.players.find(candidate => index.match(candidate));
    const pos = normalizedPosition(player.pos);
    if (pos && match?.bye != null) {
      const key = `${pos}:${match.bye}`;
      ownedByes.set(key, (ownedByes.get(key) ?? 0) + 1);
    }
  }
  const byeConflicts = new Map<string, number>();
  for (const player of board?.players ?? []) {
    if (player.bye == null) continue;
    const count = ownedByes.get(`${normalizedPosition(player.pos)}:${player.bye}`) ?? 0;
    if (count > 0) byeConflicts.set(player.key, count);
  }
  return {
    positions,
    byeConflicts,
    summary: needs.length ? `Need: ${needs.join(" · ")}` : "Starters filled · Building depth",
  };
}
