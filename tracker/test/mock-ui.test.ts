import { describe, expect, it } from "vitest";
import {
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
});
