import type { MockLifecycleStatus, MockPick, MockState } from "./mock-draft";
import { variancePresetLabel } from "./mock-ui";

export interface MockReviewTeamRoster {
  draft_slot: number;
  team_name: string;
  is_user: boolean;
  picks: MockPick[];
  position_counts: { pos: string; count: number }[];
}

export interface MockReviewModel {
  headline: string;
  user: MockReviewTeamRoster;
  rosters: MockReviewTeamRoster[];
  config: {
    seed: number;
    variance_label: string;
    user_slot: number;
    team_count: number;
    rounds: number;
    strategy_version: string;
    board_fingerprint: string;
  };
}

const CANONICAL_POSITIONS = ["QB", "RB", "WR", "TE", "K", "DEF"];
const UNKNOWN_POSITION = "—";

function positionCounts(picks: readonly MockPick[]): { pos: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const pick of picks) {
    const pos = pick.player_pos ?? UNKNOWN_POSITION;
    counts.set(pos, (counts.get(pos) ?? 0) + 1);
  }
  const ordered = [
    ...CANONICAL_POSITIONS,
    ...[...counts.keys()].filter((pos) => !CANONICAL_POSITIONS.includes(pos) && pos !== UNKNOWN_POSITION),
    UNKNOWN_POSITION,
  ];
  return ordered
    .filter((pos) => counts.has(pos))
    .map((pos) => ({ pos, count: counts.get(pos)! }));
}

export function mockReviewModel(state: MockState): MockReviewModel | null {
  const mock = state.mock;
  const teams = state.teams;
  if (!state.configured || !mock || !teams || state.lifecycle !== "complete" || state.board_error) {
    return null;
  }
  const bySlot = [...teams].sort((a, b) => a.draft_slot - b.draft_slot).map((team) => ({
    draft_slot: team.draft_slot,
    team_name: team.name,
    is_user: team.is_user,
    picks: state.picks
      .filter((pick) => pick.draft_slot === team.draft_slot)
      .sort((a, b) => a.overall_pick - b.overall_pick),
  }));
  const rosters = [...bySlot.filter((team) => team.is_user), ...bySlot.filter((team) => !team.is_user)]
    .map((team) => ({ ...team, position_counts: positionCounts(team.picks) }));
  const user = rosters.find((team) => team.is_user) ?? rosters[0]!;
  return {
    headline:
      `Mock complete · ${state.picks.length} picks · ${mock.rounds} rounds · ${mock.team_count} teams`,
    user,
    rosters,
    config: {
      seed: mock.seed,
      variance_label: variancePresetLabel(mock.variance_preset),
      user_slot: mock.user_slot,
      team_count: mock.team_count,
      rounds: mock.rounds,
      strategy_version: mock.strategy_version,
      board_fingerprint: mock.board_fingerprint,
    },
  };
}

export interface ReviewEntry {
  lifecycle: MockLifecycleStatus;
  mock_id: string;
}

/** True exactly when a state transition newly lands in review: a fresh load, a
 *  lifecycle change into complete, or a different completed mock replacing the
 *  displayed one (e.g. reconciling after another tab finished a newer mock). */
export function entersReview(previous: ReviewEntry | null, next: ReviewEntry | null): boolean {
  if (next?.lifecycle !== "complete") return false;
  return previous === null
    || previous.lifecycle !== "complete"
    || previous.mock_id !== next.mock_id;
}

function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function pickLabel(pick: MockPick): string {
  return `${pick.round}.${String(pick.round_pick).padStart(2, "0")}`;
}

function pickMeta(pick: MockPick): string {
  const pos = pick.player_pos ?? UNKNOWN_POSITION;
  return pick.player_team ? `${pos} · ${pick.player_team}` : pos;
}

export function renderMockReviewSummary(model: MockReviewModel): string {
  const { config } = model;
  return `<span class="decision-label review-eyebrow">Mock complete</span>
<p class="review-headline">${esc(model.headline)}</p>
<p class="review-user">${esc(model.user.team_name)} drafted from Slot ${config.user_slot}.</p>
<dl class="review-config">
<div><dt>Seed</dt><dd>${config.seed}</dd></div>
<div><dt>Variance</dt><dd>${esc(config.variance_label)}</dd></div>
<div><dt>Slot</dt><dd>${config.user_slot} of ${config.team_count}</dd></div>
<div><dt>Rounds</dt><dd>${config.rounds}</dd></div>
<div><dt>Strategy</dt><dd>${esc(config.strategy_version)}</dd></div>
<div><dt>Board</dt><dd>${esc(config.board_fingerprint)}</dd></div>
</dl>`;
}

export function renderMockReviewRosters(model: MockReviewModel): string {
  return model.rosters.map((roster) => {
    const picks = roster.picks.map((pick) => {
      const you = pick.source === "user" ? " you-pick" : "";
      const marker = pick.source === "user" ? '<span class="you-chip">You</span>' : "";
      return `<li class="review-pick${you}"><span class="review-slotno">${pickLabel(pick)}</span> ` +
        `<span class="review-player">${esc(pick.player_name)}</span> ` +
        `<span class="review-meta">${esc(pickMeta(pick))}</span>${marker}</li>`;
    }).join("");
    const counts = roster.position_counts.map((entry) => `${entry.pos} ${entry.count}`).join(" · ");
    const userChip = roster.is_user ? ' <span class="you-chip">YOU</span>' : "";
    return `<details class="review-team${roster.is_user ? " user" : ""}"${roster.is_user ? " open" : ""}>
<summary><span class="review-team-name">${esc(roster.team_name)}</span>${userChip}<span class="review-counts">${esc(counts)}</span></summary>
<ol class="review-picks">${picks}</ol>
</details>`;
  }).join("");
}
