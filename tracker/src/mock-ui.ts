import type { MockSetup } from "./mock-draft";
import type { VariancePreset } from "./mock-strategy";

interface ValueControl {
  value: string;
}

interface TextTarget {
  textContent: string | null;
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
