import type { DraftState } from "./draft-store";
import type { Board, Player } from "./types";
import { nextPick } from "./draft";
import { distinctPlayerIdentities, indexPlayerIdentities, normalizedPosition } from "./player-identity";
import { projectedLineup, rosterFit } from "./roster-fit";
import { availablePlayers } from "./suggestions";

export interface CandidatePriority {
  /** Lineup value now minus the expected best same-position value, this
   * player included, still available at the user's next pick; null when
   * unprojected. */
  score: number | null;
  gain: number | null;
  /** Chance this player lasts to the user's next pick; null without one. */
  survival: number | null;
  /** Unscored order: open starter need, useful depth, other. */
  need: 0 | 1 | 2;
  reason: string;
}

/** Share of a non-starting player's points counted as depth value. */
export const DEPTH_WEIGHT = 0.25;

export interface PriorityOptions {
  depthWeight?: number;
}

function normalCdf(z: number): number {
  // Abramowitz-Stegun 7.1.26 erf approximation.
  const x = Math.abs(z) / Math.SQRT2;
  const t = 1 / (1 + 0.3275911 * x);
  const erf = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-x * x);
  return z >= 0 ? (1 + erf) / 2 : (1 - erf) / 2;
}

/** P(a player is still available at overall pick `next`) from board ADP. */
export function survivalProbability(adp: number | null, adpStdev: number | null, next: number): number {
  if (adp === null) return 1;
  const sigma = adpStdev !== null && adpStdev > 0 ? adpStdev : Math.max(3, 0.1 * adp);
  return 1 - normalCdf((next - adp) / sigma);
}

/** Expected best value reachable at the next pick: take the best survivor. */
export function expectedBestAtNextPick(options: readonly { value: number; survival: number }[]): number {
  let expected = 0, gone = 1;
  for (const { value, survival } of [...options].sort((a, b) => b.value - a.value)) {
    if (value <= 0 || gone < 1e-9) break;
    expected += value * survival * gone;
    gone *= 1 - survival;
  }
  return expected;
}

function userPickAfter(draft: DraftState, after: number): number | null {
  const teams = draft.teams, rounds = draft.draft?.rounds;
  if (!teams || !rounds) return null;
  for (let overall = after + 1; overall <= teams.length * rounds; overall += 1) {
    if (nextPick(teams, rounds, overall)?.is_user) return overall;
  }
  return null;
}

function survivalPhrase(survival: number): string {
  return survival < 0.25 ? "likely gone by your next pick"
    : survival <= 0.75 ? "coin flip to last" : "should last to your next pick";
}

export interface StarterPriority {
  candidates: ReadonlyMap<string, CandidatePriority>;
  summary: string;
  byeConflicts: ReadonlyMap<string, number>;
}

export function liveStarterPriority(draft: DraftState | null, board?: Board, options: PriorityOptions = {}): StarterPriority | null {
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
  // Bench value counts only while a bench slot remains for the pick.
  const benchSlots = board.roster_slots.BN ?? 0;
  const depthWeight = players.length - before.assignments.size < benchSlots ? options.depthWeight ?? DEPTH_WEIGHT : 0;
  const current = draft.next?.overall_pick ?? draft.picks.length + 1;
  const pickNow = userPickAfter(draft, current - 1);
  const nextOwn = pickNow === null ? null : userPickAfter(draft, pickNow);
  const byeConflicts = new Map<string, number>();
  const scored = new Map<string, { player: Player; pos: string | null; gain: number | null; value: number | null; role: string; need: 0 | 1 | 2 }>();
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
    // The non-starting remainder (pure bench, or the displaced starter) is depth.
    const value = gain === null ? null : gain + depthWeight * Math.max(0, player.points! - gain);
    const slot = after.assignments.get(player.key);
    const role = slot?.includes("/") ? `${slot} flex` : `${slot ?? pos ?? "Unknown"} starter`;
    const fillsOpen = pos !== null && rosterFit(board.roster_slots, [...ownedPositions, pos]).filledStarters > beforeFit.filledStarters;
    // Depth in a used offensive position retains value through flex, injuries,
    // and byes. No fixed backup counts and no promotion of unused positions.
    const usefulDepth = pos !== null && ["RB", "WR", "TE"].includes(pos)
      && rosterFit(board.roster_slots, [pos]).filledStarters > 0;
    scored.set(player.key, { player, pos, gain, value, role, need: fillsOpen ? 0 : usefulDepth ? 1 : 2 });
  }
  // One-step lookahead: the best same-position value likely left at the next
  // own pick. The candidate counts too: a player who will last is not urgent.
  const survival = new Map<string, number>();
  const alternatives = new Map<string, { key: string; value: number; survival: number }[]>();
  if (nextOwn !== null) {
    const taken = draft.picks.map(pick => ({ key: pick.player_key, name: pick.player_name, pos: pick.player_pos, team: pick.player_team }));
    for (const player of availablePlayers(board.players, taken)) {
      const entry = scored.get(player.key)!;
      const chance = survivalProbability(player.adp, player.adp_stdev, nextOwn);
      survival.set(player.key, chance);
      if (entry.value === null || entry.value <= 0 || entry.pos === null) continue;
      const list = alternatives.get(entry.pos) ?? [];
      list.push({ key: player.key, value: entry.value, survival: chance });
      alternatives.set(entry.pos, list);
    }
  }
  const candidates = new Map<string, CandidatePriority>();
  for (const { player, pos, gain, value, role, need } of scored.values()) {
    const later = pos === null ? [] : alternatives.get(pos) ?? [];
    const score = value === null ? null : Math.round((value - expectedBestAtNextPick(later)) * 10) / 10;
    const chance = nextOwn === null ? null : survival.get(player.key) ?? survivalProbability(player.adp, player.adp_stdev, nextOwn);
    const phrase = chance === null || !value ? "" : ` · ${survivalPhrase(chance)}`;
    const reason = gain === null
      ? `${need === 0 ? "Open starter/flex need" : "Depth"} · ${incomplete ? "roster projections incomplete" : "projection unavailable"}`
      : gain > 0 ? `${role} · +${gain.toFixed(1)} lineup pts${phrase}`
      : value! > 0 ? `${pos ?? "Unknown"} depth · +${value!.toFixed(1)} (bench)${phrase}`
      : "Depth · no projected lineup gain";
    candidates.set(player.key, { score, gain, survival: chance, need, reason });
  }
  const needs = [...before.open].map(([slot, count]) => `${slot} ${count}`);
  return {
    candidates,
    byeConflicts,
    summary: (needs.length ? `Need: ${needs.join(" · ")}` : "Starters filled · Building depth")
      + (incomplete ? " · roster projections incomplete" : ""),
  };
}

/** Row order the live board uses once the user owns a pick: opportunity
 * score first, then board rank softened by bye overlaps. Unscored rows follow,
 * open starter needs before depth. */
export function lineupPriorityOrder(
  priority: ReadonlyMap<string, CandidatePriority>,
  byeConflicts?: ReadonlyMap<string, number>,
): (a: Player, b: Player) => number {
  const soft = (p: Player): number => p.rank + 5 * (byeConflicts?.get(p.key) ?? 0);
  return (a, b) => {
    const left = priority.get(a.key), right = priority.get(b.key);
    const ls = left?.score ?? null, rs = right?.score ?? null;
    if ((ls === null) !== (rs === null)) return ls === null ? 1 : -1;
    return (ls !== null ? rs! - ls : (left?.need ?? 2) - (right?.need ?? 2))
      || soft(a) - soft(b) || a.rank - b.rank;
  };
}
