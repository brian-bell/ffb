import type { MockLifecycleStatus, MockPick, MockSetup } from "./mock-draft";
import { draftClockPresentation, nextPick, type DraftClockPresentation, type NextPick } from "./draft";
import type { VariancePreset } from "./mock-strategy";
import type { PlayerPool } from "./player-pool";
import { safePositionsForTurn } from "./roster-fit";
import { suggestAvailablePlayers } from "./suggestions";
import type { Board, Player } from "./types";

interface ValueControl {
  value: string;
}

interface TextTarget {
  textContent: string | null;
}

export interface MockActionInput {
  lifecycle: MockLifecycleStatus;
  can_undo: boolean;
  next: NextPick | null;
  writing: boolean;
  board_available: boolean;
}

export interface MockActionState {
  status_label: "Active" | "Paused" | "Complete";
  can_pick: boolean;
  lifecycle_label: "Pause" | "Resume";
  lifecycle_enabled: boolean;
  undo_enabled: boolean;
  reset_enabled: boolean;
  discard_enabled: boolean;
}

export function mockActionState(input: MockActionInput): MockActionState {
  const ready = input.board_available && !input.writing;
  return {
    status_label: input.lifecycle === "complete"
      ? "Complete"
      : input.lifecycle === "paused"
      ? "Paused"
      : "Active",
    can_pick: ready && input.lifecycle === "active" && input.next?.is_user === true,
    lifecycle_label: input.lifecycle === "paused" ? "Resume" : "Pause",
    lifecycle_enabled: ready && input.lifecycle !== "complete",
    undo_enabled: ready && input.can_undo,
    reset_enabled: ready,
    discard_enabled: !input.writing,
  };
}

export interface MockSuggestionInput {
  board: Board;
  pool: PlayerPool;
  picks: readonly MockPick[];
  lifecycle: MockLifecycleStatus;
  next: NextPick | null;
  user_slot: number;
}

/** Market-leading players whose positions keep every league roster completable. */
export function mockSuggestions(input: MockSuggestionInput): Player[] {
  if (input.lifecycle !== "active" || input.next?.is_user !== true) return [];
  const safe = safePositionsForTurn({
    rosterSlots: input.board.roster_slots,
    teamCount: input.board.num_teams,
    picks: input.picks,
    available: input.pool.available,
    draftSlot: input.user_slot - 1,
  });
  return suggestAvailablePlayers(
    input.pool.available.filter((player) => player.pos !== null && safe.has(player.pos)),
  );
}

export interface MockClockState {
  presentation: DraftClockPresentation | null;
  summary: string;
}

export function mockClockState(
  lifecycle: MockLifecycleStatus,
  teams: Parameters<typeof nextPick>[0],
  rounds: number,
  next: NextPick | null,
): MockClockState {
  if (!next) return { presentation: null, summary: "Draft complete" };
  const following = nextPick(teams, rounds, next.overall_pick + 1);
  return {
    presentation: draftClockPresentation(next, following?.team_name ?? null),
    summary: `${lifecycle === "paused" ? "Paused · " : ""}Round ${next.round} of ${rounds} · Pick ${next.overall_pick} of ${teams.length * rounds} · ${next.team_name} is on the clock`,
  };
}

export function readMockSetupControls(
  slot: ValueControl,
  seed: ValueControl,
  variance: ValueControl,
): MockSetup {
  return {
    user_slot: Number(slot.value),
    seed: Number(seed.value),
    variance_preset: variance.value as VariancePreset,
  };
}

export function variancePresetLabel(preset: VariancePreset): string {
  return `${preset[0]!.toUpperCase()}${preset.slice(1)}`;
}

export function renderVariancePreset(target: TextTarget, preset: VariancePreset): void {
  target.textContent = variancePresetLabel(preset);
}

export function mockErrorMessage(value: unknown, fallback: string): string {
  if (value && typeof value === "object" && "message" in value) {
    const message = (value as { message?: unknown }).message;
    if (typeof message === "string") return message;
  }
  return fallback;
}

export function renderMockError(target: TextTarget, value: unknown, fallback: string): void {
  target.textContent = mockErrorMessage(value, fallback);
}
