import { describe, expect, it } from "vitest";
import {
  mockActionState,
  readMockSetupControls,
  renderMockError,
  renderVariancePreset,
} from "../src/mock-ui";

describe("mock UI controller", () => {
  it.each(["calm", "realistic", "wild"] as const)(
    "reads and submits the lowercase %s preset from the actual control seam",
    (preset) => {
      expect(readMockSetupControls(
        { value: "4" },
        { value: "8042" },
        { value: preset },
      )).toEqual({ user_slot: 4, seed: 8042, variance_preset: preset });
    },
  );

  it("renders the saved preset and preserves an illegal-pick error during reconciliation", () => {
    const status = { textContent: "" };
    const error = { textContent: "" };
    renderMockError(
      error,
      { error: "illegal_roster_pick", message: "That pick makes a roster impossible." },
      "fallback",
    );
    renderVariancePreset(status, "realistic");

    expect(status.textContent).toBe("Realistic");
    expect(error.textContent).toBe("That pick makes a roster impossible.");
  });

  it.each([
    ["active", true, false, "Active", true, "Pause", true, true, true],
    ["paused", true, false, "Paused", false, "Resume", true, true, true],
    ["complete", true, false, "Complete", false, "Pause", false, true, true],
    ["paused", false, false, "Paused", false, "Resume", false, false, false],
    ["active", true, true, "Active", false, "Pause", false, false, false],
  ] as const)(
    "derives lifecycle controls for %s (board=%s, writing=%s)",
    (lifecycle, boardAvailable, writing, label, canPick, toggleLabel, toggleEnabled, undoEnabled, resetEnabled) => {
      expect(mockActionState({
        lifecycle,
        can_undo: true,
        next: lifecycle === "complete"
          ? null
          : { overall_pick: 1, round: 1, round_pick: 1, team_id: 1, team_name: "Brian", is_user: true, direction: "forward" },
        writing,
        board_available: boardAvailable,
      })).toEqual({
        status_label: label,
        can_pick: canPick,
        lifecycle_label: toggleLabel,
        lifecycle_enabled: toggleEnabled,
        undo_enabled: undoEnabled,
        reset_enabled: resetEnabled,
        discard_enabled: !writing,
      });
    },
  );
});
