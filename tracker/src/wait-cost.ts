import { nextPick, type DraftTeam } from "./draft";
import type { DraftState } from "./draft-store";
import type { MockState } from "./mock-draft";
import { buildPlayerPool } from "./player-pool";
import { normalizedPosition } from "./player-identity";
import { liveStarterPriority } from "./starter-priority";
import type { Board, Player } from "./types";

export function nextUserTurn(teams: DraftTeam[], rounds: number, current: number): { pick: number; opponents: number } | null {
  for (let pick = current + 1; pick <= rounds * teams.length; pick++) {
    if (nextPick(teams, rounds, pick)?.is_user) return { pick, opponents: pick - current - 1 };
  }
  return null;
}

export type Availability = "at risk" | "reasonable later" | "uncertain" | "unknown";
const positive = (n: number | null): n is number => n !== null && Number.isFinite(n) && n > 0;

/** Evidence envelope, NOT a fitted distribution or a probability interval. */
export function marketAvailability(player: Player, pick: number, teams: number): Availability {
  if (!positive(player.adp)) return "unknown";
  // Always allow at least one league round of drift. Observed extremes and
  // dispersion can widen this buffer, but never create unwarranted certainty.
  const spread = Math.max(teams, positive(player.adp_stdev) ? player.adp_stdev : 0);
  const evidence = [player.adp_high, player.adp_low].filter(positive);
  const early = Math.min(player.adp - spread, ...evidence);
  const late = Math.max(player.adp + spread, ...evidence);
  return late < pick ? "at risk" : early > pick ? "reasonable later" : "uncertain";
}

export interface WaitingCost {
  gain: number | null;
  dropOff: number | null;
  alternative: string | null;
  availability: Availability;
  text: string;
}

/** Read-only selection advice, shared by live and saved-board mock sessions. */
export function waitingCost(draft: DraftState | null, board: Board | null, key: string): WaitingCost | null {
  if (!draft?.configured || !draft.next?.is_user || !draft.teams || !draft.draft || !board) return null;
  const pool = buildPlayerPool(board.players, draft.picks);
  const player = pool.available.find(p => p.key === key);
  if (!player) return null;
  const priority = liveStarterPriority(draft, board)!;
  const candidate = priority.candidates.get(key)!;
  const turn = nextUserTurn(draft.teams, draft.draft.rounds, draft.next.overall_pick);
  const base = `Take now: ${candidate.reason}. `;
  const empty = { gain: candidate.gain, dropOff: null, alternative: null, availability: "unknown" as Availability };
  if (!turn) return { ...empty, text: base + "This is your final pick; there is no later turn to wait for." };
  if (turn.opponents === 0) return { ...empty, dropOff: 0, availability: "reasonable later", text: base + `Your next pick is #${turn.pick}, with no opponent picks between. No market loss from waiting one pick.` };
  const availability = marketAvailability(player, turn.pick, draft.teams.length);
  const later = pool.available.filter(p => normalizedPosition(p.pos) === normalizedPosition(player.pos)
    && marketAvailability(p, turn.pick, draft.teams!.length) === "reasonable later");
  const known = later.filter(p => priority.candidates.get(p.key)?.gain != null)
    .sort((a, b) => priority.candidates.get(b.key)!.gain! - priority.candidates.get(a.key)!.gain! || a.key.localeCompare(b.key));
  const alternative = known[0];
  // Missing projections in the comparison pool prevent a defensible best-alternative estimate.
  const dropOff = candidate.gain === null || !alternative || known.length !== later.length ? null
    : Math.max(0, candidate.gain - priority.candidates.get(alternative.key)!.gain!);
  const market = `Next turn #${turn.pick}: ${turn.opponents} opponent picks. Market forecast: ${availability}${positive(player.adp) ? ` (ADP ${player.adp.toFixed(1)})` : " (ADP unavailable)"}. `;
  const comparison = dropOff === null
    ? "Positional drop-off unknown: no adequately projected later comparison. "
    : `Waiting scenario: ${alternative!.name} at ${player.pos}, +${priority.candidates.get(alternative!.key)!.gain!.toFixed(1)} lineup pts; ${dropOff.toFixed(1)} lineup pts of positional drop-off. `;
  const advice = candidate.gain === null ? "Recommendation: projections incomplete; use roster need without a numeric waiting-cost claim. "
    : candidate.gain === 0 ? "Recommendation: no projected starter/flex gain; scarcity alone does not justify this depth pick. "
    : availability === "reasonable later" ? "Recommendation: Waiting is plausible; compare another position's lineup gain and drop-off. "
    : availability === "at risk" && dropOff !== null && dropOff > 0 ? "Recommendation: consider taking now to protect this lineup gain; compare other positions' drop-offs before passing on more points. "
    : "Recommendation: timing evidence is insufficient to favor taking now; retain lineup-gain priority. ";
  return { gain: candidate.gain, dropOff, alternative: alternative?.key ?? null, availability,
    text: base + market + comparison + advice + "ADP range/dispersion widens a one-round buffer; this is a scenario, not a probability or availability guarantee." };
}

export function mockWaitingCost(mock: MockState | null, board: Board | null, key: string): WaitingCost | null {
  if (!mock?.mock || mock.lifecycle !== "active") return null;
  return waitingCost({ ...mock, draft: { name: "Mock", rounds: mock.mock.rounds, team_count: mock.mock.team_count },
    picks: mock.picks.map(p => ({ ...p, team_id: mock.teams?.find(t => t.draft_slot === p.draft_slot)?.id ?? -1, picked_at: "" })),
  }, board, key);
}
