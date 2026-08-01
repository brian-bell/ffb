import type { MockLifecycleStatus, MockSetup } from "./mock-draft";
import type { NextPick } from "./draft";
import type { VariancePreset } from "./mock-strategy";

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
