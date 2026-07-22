import { nextPick } from "./draft";
import { isValidBoard } from "./board";
import { isAvailable, normalizedPosition, type PlayerIdentity } from "./player-identity";
import type { DraftState, RecordedPick } from "./draft-store";
import type { Board, Player } from "./types";

const DEDICATED = ["QB", "RB", "WR", "TE", "K", "DEF"] as const;
const FLEX = new Set(["RB", "WR", "TE"]);
type Need = "dedicated" | "flex" | "depth";

export interface RosterNeeds {
  picked: Record<string, number>;
  openDedicated: Record<string, number>;
  openFlex: number;
  openStarters: number;
  benchPicks: number;
  unknownPicks: number;
  unsupportedOpen: Record<string, number>;
}

export interface RecommendationContext {
  roster: RosterNeeds;
  nextUserOverallPick: number | null;
  picksUntilNextTurn: number | null;
  remainingUserPicks: number;
}

export interface DraftRecommendation {
  player: Player;
  position: string;
  need: Need;
  forced: boolean;
  tier: number | null;
  tierRemaining: number | null;
  tierAtRisk: boolean;
  nextTierVorpDrop: number | null;
  marketUrgent: boolean | null;
  reason: string;
}

export interface RecommendationState {
  context: RecommendationContext | null;
  recommendation: DraftRecommendation | null;
  warnings: string[];
}

type PickPosition = Pick<RecordedPick, "player_pos">;

function positiveCount(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? Math.floor(value) : 0;
}

function slotCounts(slots: Readonly<Record<string, number>>): { dedicated: Record<string, number>; flex: number; unsupported: Record<string, number> } {
  const dedicated = Object.fromEntries(DEDICATED.map((pos) => [pos, 0])) as Record<string, number>;
  const unsupported: Record<string, number> = {};
  let flex = 0;
  for (const [raw, value] of Object.entries(slots)) {
    const count = positiveCount(value);
    if (!count || raw === "BN") continue;
    const position = normalizedPosition(raw);
    if (position && DEDICATED.includes(position as (typeof DEDICATED)[number])) dedicated[position] += count;
    else if (raw === "W/R/T") flex += count;
    else unsupported[raw] = count;
  }
  return { dedicated, flex, unsupported };
}

/** Allocate persisted pick positions exactly once, dedicated starters before FLEX. */
export function rosterNeeds(rosterSlots: Readonly<Record<string, number>>, picks: readonly PickPosition[]): RosterNeeds {
  const slots = slotCounts(rosterSlots);
  const picked: Record<string, number> = {};
  let unknownPicks = 0;
  for (const pick of picks) {
    const pos = normalizedPosition(pick.player_pos);
    if (!pos) {
      unknownPicks += 1;
      continue;
    }
    picked[pos] = (picked[pos] ?? 0) + 1;
  }
  const openDedicated: Record<string, number> = {};
  let starterPicks = 0;
  const leftovers: Record<string, number> = {};
  for (const pos of DEDICATED) {
    const used = Math.min(picked[pos] ?? 0, slots.dedicated[pos]);
    starterPicks += used;
    leftovers[pos] = Math.max(0, (picked[pos] ?? 0) - used);
    openDedicated[pos] = Math.max(0, slots.dedicated[pos] - used);
  }
  const flexUsed = Math.min(slots.flex, [...FLEX].reduce((total, pos) => total + (leftovers[pos] ?? 0), 0));
  starterPicks += flexUsed;
  return {
    picked,
    openDedicated,
    openFlex: slots.flex - flexUsed,
    openStarters: Object.values(openDedicated).reduce((total, count) => total + count, 0) + slots.flex - flexUsed,
    benchPicks: picks.length - starterPicks,
    unknownPicks,
    unsupportedOpen: slots.unsupported,
  };
}

function snapshot(pick: RecordedPick): PlayerIdentity {
  return { key: pick.player_key, name: pick.player_name, pos: pick.player_pos, team: pick.player_team };
}

function playerOrder(a: Player, b: Player): number {
  const aVorp = typeof a.vorp === "number" ? a.vorp : Number.NEGATIVE_INFINITY;
  const bVorp = typeof b.vorp === "number" ? b.vorp : Number.NEGATIVE_INFINITY;
  return bVorp - aVorp || a.rank - b.rank || a.name.localeCompare(b.name);
}

function lookAhead(state: DraftState, teamId: number): Pick<RecommendationContext, "nextUserOverallPick" | "picksUntilNextTurn" | "remainingUserPicks"> {
  if (!state.next || !state.draft || !state.teams) return { nextUserOverallPick: null, picksUntilNextTurn: null, remainingUserPicks: 0 };
  const total = state.draft.rounds * state.draft.team_count;
  let nextUserOverallPick: number | null = null;
  let picksUntilNextTurn: number | null = null;
  let remainingUserPicks = 0;
  for (let overall = state.next.overall_pick; overall <= total; overall += 1) {
    const pick = nextPick(state.teams, state.draft.rounds, overall);
    if (pick?.team_id !== teamId) continue;
    remainingUserPicks += 1;
    if (overall > state.next.overall_pick && nextUserOverallPick === null) {
      nextUserOverallPick = overall;
      picksUntilNextTurn = overall - state.next.overall_pick - 1;
    }
  }
  return { nextUserOverallPick, picksUntilNextTurn, remainingUserPicks };
}

function needFor(pos: string, roster: RosterNeeds): Need {
  if ((roster.openDedicated[pos] ?? 0) > 0) return "dedicated";
  if (FLEX.has(pos) && roster.openFlex > 0) return "flex";
  return "depth";
}

function reasonFor(candidate: Omit<DraftRecommendation, "reason">): string {
  const tier = candidate.tier === null ? "untiered" : `${candidate.tierRemaining} player${candidate.tierRemaining === 1 ? "" : "s"} remain in Tier ${candidate.tier}`;
  if (candidate.forced && candidate.need !== "depth") return `Must fill ${candidate.position}: ${tier}.`;
  if (candidate.need === "dedicated") return `Fills your ${candidate.position} starter; ${tier}.`;
  if (candidate.need === "flex") return `Fills FLEX; ${tier}.`;
  if (candidate.nextTierVorpDrop !== null) return `Best depth value; the next ${candidate.position} tier is ${candidate.nextTierVorpDrop.toFixed(1)} VORP lower.`;
  return `Best depth value; ${tier}.`;
}

function comparator(a: DraftRecommendation, b: DraftRecommendation): number {
  const aForced = a.forced && a.need !== "depth";
  const bForced = b.forced && b.need !== "depth";
  if (aForced !== bForced) return aForced ? -1 : 1;
  const needRank: Record<Need, number> = { dedicated: 0, flex: 1, depth: 2 };
  if (needRank[a.need] !== needRank[b.need]) return needRank[a.need] - needRank[b.need];
  if (a.tierAtRisk !== b.tierAtRisk) return a.tierAtRisk ? -1 : 1;
  const aDrop = a.nextTierVorpDrop ?? Number.NEGATIVE_INFINITY;
  const bDrop = b.nextTierVorpDrop ?? Number.NEGATIVE_INFINITY;
  if (aDrop !== bDrop) return bDrop - aDrop;
  if (a.marketUrgent !== b.marketUrgent) return a.marketUrgent ? -1 : 1;
  return playerOrder(a.player, b.player);
}

/** Derive a Brian-only recommendation from immutable board value and live D1 facts. */
export function deriveRecommendation(board: Board, state: DraftState): RecommendationState {
  if (!isValidBoard(board)) return { context: null, recommendation: null, warnings: ["Board data is malformed; republish the board."] };
  if (!state.configured || !state.draft || !state.teams || !state.next) return { context: null, recommendation: null, warnings: [] };
  const user = state.teams.find((team) => team.is_user);
  if (!user) return { context: null, recommendation: null, warnings: ["No user team is configured."] };
  const roster = rosterNeeds(board.roster_slots ?? {}, state.picks.filter((pick) => pick.team_id === user.id));
  const look = lookAhead(state, user.id);
  const context: RecommendationContext = { roster, ...look };
  const warnings = [
    ...Object.entries(roster.unsupportedOpen).map(([slot, count]) => `${count} unsupported ${slot} starter slot${count === 1 ? "" : "s"} remain.`),
    ...(roster.unknownPicks ? [`${roster.unknownPicks} user pick${roster.unknownPicks === 1 ? " has" : "s have"} an unknown position.`] : []),
  ];
  if (!state.next.is_user) return { context, recommendation: null, warnings };
  const available = board.players.filter((player) => isAvailable(player, state.picks.map(snapshot)));
  const candidates: DraftRecommendation[] = [];
  for (const position of DEDICATED) {
    const need = needFor(position, roster);
    if ((position === "K" || position === "DEF") && (need === "depth" || look.remainingUserPicks > roster.openStarters)) continue;
    const player = available.filter((row) => normalizedPosition(row.pos) === position).sort(playerOrder)[0];
    if (!player) {
      if (need !== "depth") warnings.push(`No available ${position} can fill an open starter slot.`);
      continue;
    }
    const tierRows = player.tier === null ? [] : available.filter((row) => normalizedPosition(row.pos) === position && row.tier === player.tier);
    const later = player.tier === null ? [] : available.filter((row) => normalizedPosition(row.pos) === position && row.tier !== null && row.tier > player.tier!).sort(playerOrder);
    const nextTierVorpDrop = typeof player.vorp === "number" && typeof later[0]?.vorp === "number" ? player.vorp - later[0].vorp : null;
    const tierRemaining = player.tier === null ? null : tierRows.length;
    const tierAtRisk = tierRemaining !== null && (look.picksUntilNextTurn === null || tierRemaining <= look.picksUntilNextTurn);
    const marketUrgent = look.nextUserOverallPick === null ? null : typeof player.adp === "number" && player.adp <= look.nextUserOverallPick;
    const candidate: Omit<DraftRecommendation, "reason"> = {
      player, position, need, forced: look.remainingUserPicks <= roster.openStarters,
      tier: player.tier, tierRemaining, tierAtRisk, nextTierVorpDrop, marketUrgent,
    };
    candidates.push({ ...candidate, reason: reasonFor(candidate) });
  }
  return { context, recommendation: candidates.sort(comparator)[0] ?? null, warnings };
}
