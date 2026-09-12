import type { DraftState } from "./draft-store";
import type { Board } from "./types";
import { distinctPlayerIdentities, indexPlayerIdentities, normalizedPosition } from "./player-identity";
import { projectedLineup, rosterFit } from "./roster-fit";

export interface CandidatePriority {
  /** Measured improvement, unprojected open need, useful depth, other depth. */
  group: 0 | 1 | 2 | 3;
  gain: number | null;
  reason: string;
}

export interface StarterPriority {
  candidates: ReadonlyMap<string, CandidatePriority>;
  summary: string;
  byeConflicts: ReadonlyMap<string, number>;
}

export function liveStarterPriority(draft: DraftState | null, board?: Board): StarterPriority | null {
  const user = draft?.configured ? draft.teams?.find(team => team.is_user) : undefined;
  if (!user || !draft || !board) return null;
  const identities = distinctPlayerIdentities(draft.picks
    .filter(pick => pick.team_id === user.id)
    .map(pick => ({ key: pick.player_key, name: pick.player_name, pos: pick.player_pos, team: pick.player_team })));
  const players = identities.map(player => {
    const index = indexPlayerIdentities([player], board.players);
    const match = board.players.find(candidate => candidate.key === player.key)
      ?? board.players.find(candidate => index.match(candidate));
    return { ...player, pos: normalizedPosition(player.pos), points: match?.points ?? null, bye: match?.bye ?? null };
  });
  const before = projectedLineup(board.roster_slots, players);
  const incomplete = players.some(player => player.points === null);
  const ownedPositions = players.flatMap(player => player.pos ? [player.pos] : []);
  const beforeFit = rosterFit(board.roster_slots, ownedPositions);
  const ownedByes = new Map<string, number>();
  for (const player of players) {
    if (player.pos && player.bye != null) {
      const key = `${player.pos}:${player.bye}`;
      ownedByes.set(key, (ownedByes.get(key) ?? 0) + 1);
    }
  }
  const byeConflicts = new Map<string, number>();
  const candidates = new Map<string, CandidatePriority>();
  for (const player of board.players) {
    const pos = normalizedPosition(player.pos);
    if (player.bye != null) {
      const count = ownedByes.get(`${pos}:${player.bye}`) ?? 0;
      if (count > 0) byeConflicts.set(player.key, count);
    }
    const after = projectedLineup(board.roster_slots, [...players, { ...player, pos }]);
    // Avoid summation noise deciding a tie before the bye/rank tiebreak.
    const gain = incomplete || player.points === null ? null
      : Math.max(0, Math.round((after.total - before.total) * 1e6) / 1e6);
    const slot = after.assignments.get(player.key);
    const role = slot?.includes("/") ? `${slot} flex` : `${slot ?? pos ?? "Unknown"} starter`;
    const fillsOpen = pos !== null && rosterFit(board.roster_slots, [...ownedPositions, pos]).filledStarters > beforeFit.filledStarters;
    // Depth in a used offensive position retains value through flex, injuries,
    // and byes. No fixed backup counts and no promotion of unused positions.
    const usefulDepth = pos !== null && ["RB", "WR", "TE"].includes(pos)
      && rosterFit(board.roster_slots, [pos]).filledStarters > 0;
    const group = gain !== null && gain > 0 ? 0 : gain === null && fillsOpen ? 1 : usefulDepth ? 2 : 3;
    const reason = group === 0 ? `${role} · +${gain!.toFixed(1)} lineup pts`
      : gain === null ? `${fillsOpen ? "Open starter/flex need" : "Depth"} · ${incomplete ? "roster projections incomplete" : "projection unavailable"}`
      : "Depth · no projected lineup gain";
    candidates.set(player.key, { group, gain, reason });
  }
  const needs = [...before.open].map(([slot, count]) => `${slot} ${count}`);
  return {
    candidates,
    byeConflicts,
    summary: (needs.length ? `Need: ${needs.join(" · ")}` : "Starters filled · Building depth")
      + (incomplete ? " · roster projections incomplete" : ""),
  };
}
