// Offline draft-recommendation backtest: score rankers against a saved draft.
// Pure compute over board.json and a saved DraftState; no I/O, no D1, no KV.
import { nextPick } from "./draft";
import type { DraftState, RecordedPick } from "./draft-store";
import { indexPlayerIdentities } from "./player-identity";
import { projectedLineup } from "./roster-fit";
import { liveStarterPriority, lineupPriorityOrder, type PriorityOptions } from "./starter-priority";
import { availablePlayers, marketOrder } from "./suggestions";
import type { Board, Player } from "./types";

export interface RankerContext {
  board: Board;
  /** Draft state at the user's turn: every earlier pick, none later. */
  draft: DraftState;
  /** Board players not yet taken, in board order. */
  available: readonly Player[];
}

/** A ranker returns the available pool best-first; index 0 is its recommendation. */
export interface Ranker {
  name: string;
  order(context: RankerContext): readonly Player[];
  /** Optional per-turn note explaining a recommended player. */
  explain?(context: RankerContext, player: Player): string;
}

export interface PickComparison {
  overall_pick: number;
  round: number;
  actual: Player | null;
  recommended: Player | null;
  agreed: boolean;
  /** Projected starting-lineup gain of each choice given the roster actually held then. */
  actualGain: number | null;
  recommendedGain: number | null;
  recommendedReason?: string;
}

export interface RankerResult {
  ranker: string;
  /** Final projected starting lineup had every own pick followed this ranker. */
  followedTotal: number;
  followedRoster: Player[];
  picks: PickComparison[];
  /** Share of recorded own picks that matched the recommendation at that turn. */
  agreement: number;
}

export interface BacktestReport {
  draft: string;
  user: string;
  actualTotal: number;
  actualRoster: Player[];
  rankers: RankerResult[];
}

/** The same saved draft seen from another team's seat. */
export function asUserTeam(saved: DraftState, teamId: number): DraftState {
  if (!saved.teams?.some(team => team.id === teamId)) throw new Error(`No team ${teamId} in saved draft.`);
  return { ...saved, teams: saved.teams.map(team => ({ ...team, is_user: team.id === teamId })) };
}

function boardPlayer(board: Board, pick: RecordedPick): Player | null {
  const byKey = board.players.find(p => p.key === pick.player_key);
  if (byKey) return byKey;
  const index = indexPlayerIdentities([{ key: pick.player_key, name: pick.player_name, pos: pick.player_pos, team: pick.player_team }], board.players);
  return board.players.find(candidate => index.match(candidate)) ?? null;
}

function lineupTotal(board: Board, roster: readonly Player[]): number {
  return projectedLineup(board.roster_slots, roster).total;
}

function gainOf(board: Board, roster: readonly Player[], player: Player | null): number | null {
  if (!player || player.points === null || roster.some(p => p.points === null)) return null;
  return Math.max(0, Math.round((lineupTotal(board, [...roster, player]) - lineupTotal(board, roster)) * 1e6) / 1e6);
}

function pickFor(turn: RecordedPick, player: Player): RecordedPick {
  return { ...turn, player_key: player.key, player_name: player.name, player_pos: player.pos, player_team: player.team };
}

function stateAt(saved: DraftState, picks: RecordedPick[], turn: RecordedPick): DraftState {
  const next = nextPick(saved.teams!, saved.draft!.rounds, turn.overall_pick);
  return { ...saved, picks, next, complete: false, revision: picks.length };
}

/**
 * Score rankers against a completed saved draft. For each recorded own pick,
 * the ranker is asked what it would have chosen given the actual history to
 * that point. Separately, the whole draft is replayed with every own pick
 * following the ranker while opponents' recorded picks stay fixed; a recorded
 * opponent pick the ranker already took simply vanishes from that opponent.
 */
export function backtestDraft(saved: DraftState, board: Board, rankers: readonly Ranker[]): BacktestReport {
  const user = saved.configured ? saved.teams?.find(team => team.is_user) : undefined;
  if (!user || !saved.draft) throw new Error("Backtest needs a configured saved draft with one user team.");
  const own = (pick: RecordedPick): boolean => pick.team_id === user.id;
  const actualRoster = saved.picks.filter(own).map(pick => boardPlayer(board, pick)).filter((p): p is Player => p !== null);

  const results = rankers.map((ranker): RankerResult => {
    // Per-turn comparison against the recorded history.
    const picks: PickComparison[] = [];
    let held: Player[] = [];
    saved.picks.forEach((pick, index) => {
      if (!own(pick)) return;
      const history = saved.picks.slice(0, index);
      const context: RankerContext = { board, draft: stateAt(saved, history, pick), available: availablePlayers(board.players, history.map(p => ({ key: p.player_key, name: p.player_name, pos: p.player_pos, team: p.player_team }))) };
      const recommended = ranker.order(context)[0] ?? null;
      const recommendedReason = recommended && ranker.explain ? ranker.explain(context, recommended) : undefined;
      const actual = boardPlayer(board, pick);
      picks.push({
        overall_pick: pick.overall_pick, round: pick.round, actual, recommended,
        agreed: actual !== null && recommended !== null && actual.key === recommended.key,
        actualGain: gainOf(board, held, actual), recommendedGain: gainOf(board, held, recommended),
        ...(recommendedReason === undefined ? {} : { recommendedReason }),
      });
      if (actual) held = [...held, actual];
    });

    // Followed replay with opponents fixed.
    const replayed: RecordedPick[] = [];
    const taken = new Set<string>();
    const followedRoster: Player[] = [];
    for (const pick of saved.picks) {
      if (!own(pick)) {
        if (taken.has(pick.player_key)) continue;
        taken.add(pick.player_key);
        replayed.push(pick);
        continue;
      }
      const available = availablePlayers(board.players, replayed.map(p => ({ key: p.player_key, name: p.player_name, pos: p.player_pos, team: p.player_team })));
      const choice = ranker.order({ board, draft: stateAt(saved, [...replayed], pick), available })[0];
      if (!choice) continue;
      taken.add(choice.key);
      followedRoster.push(choice);
      replayed.push(pickFor(pick, choice));
    }
    return {
      ranker: ranker.name,
      followedTotal: lineupTotal(board, followedRoster),
      followedRoster,
      picks,
      agreement: picks.length ? picks.filter(p => p.agreed).length / picks.length : 0,
    };
  });

  return { draft: saved.draft.name, user: user.name, actualTotal: lineupTotal(board, actualRoster), actualRoster, rankers: results };
}

/** Published board order: VORP-ranked with ADP-only players last. */
export const boardRankRanker: Ranker = {
  name: "board",
  order: ({ available }) => [...available].sort((a, b) => a.rank - b.rank),
};

/** Market order: ADP first, as the mock suggestions use. */
export const marketRanker: Ranker = {
  name: "market",
  order: ({ available }) => [...available].sort(marketOrder),
};

/** The live Available ordering: board order until the first own pick, then
 * opportunity-cost score with bye-clash softened board rank. */
export function liveLineupRanker(options: PriorityOptions = {}, name = "live"): Ranker {
  return {
    name,
    order: ({ board, draft, available }) => {
      const user = draft.teams?.find(team => team.is_user);
      const priority = liveStarterPriority(draft, board, options);
      const owned = draft.picks.some(pick => pick.team_id === user?.id);
      if (!priority || !owned) {
        const bye = priority?.byeConflicts;
        return [...available].sort((a, b) => (a.rank + 5 * (bye?.get(a.key) ?? 0)) - (b.rank + 5 * (bye?.get(b.key) ?? 0)) || a.rank - b.rank);
      }
      return [...available].sort(lineupPriorityOrder(priority.candidates, priority.byeConflicts));
    },
    explain: ({ board, draft }, player) => {
      const candidate = liveStarterPriority(draft, board, options)?.candidates.get(player.key);
      if (!candidate) return "";
      const survival = candidate.survival === null ? "" : ` · survive ${(candidate.survival * 100).toFixed(0)}%`;
      return `score ${candidate.score?.toFixed(1) ?? "—"}${survival} · ${candidate.reason}`;
    },
  };
}

export const builtinRankers: readonly Ranker[] = [boardRankRanker, marketRanker, liveLineupRanker()];
