import { describe, expect, it } from "vitest";
import {
  mockClockState,
  mockActionState,
  mockSuggestions,
  readMockSetupControls,
  renderMockError,
  renderVariancePreset,
} from "../src/mock-ui";
import { buildPlayerPool } from "../src/player-pool";
import type { Board } from "../src/types";

describe("mock UI controller", () => {
  it("suggests only market-leading positions that keep Brian's roster completable", () => {
    const board = {
      version: 1, season: 2026, generated_at: "now", scoring: "Half PPR",
      num_teams: 2, roster_slots: { QB: 1 },
      players: [
        { key: "rb", name: "Illegal RB", pos: "RB", team: "BUF", rank: 1, adp: 1, adp_rank: 1 },
        { key: "qb2", name: "Second QB", pos: "QB", team: "MIA", rank: 3, adp: 3, adp_rank: 3 },
        { key: "qb1", name: "First QB", pos: "QB", team: "NYJ", rank: 2, adp: 2, adp_rank: 2 },
      ].map((player) => ({
        bye: null, points: 1, n_sources: 1, vorp: 1, tier: 1, pos_rank: 1,
        adp_high: null, adp_low: null, adp_stdev: null, matched: true, ...player,
      })),
    } as Board;
    const input = {
      board,
      pool: buildPlayerPool(board.players, []),
      picks: [],
      lifecycle: "active" as const,
      next: { overall_pick: 1, round: 1, round_pick: 1, team_id: 1, team_name: "Brian", is_user: true, direction: "forward" as const },
      user_slot: 1,
    };

    expect(mockSuggestions(input).map((player) => player.key)).toEqual(["qb1", "qb2"]);
    expect(mockSuggestions({ ...input, lifecycle: "paused" })).toEqual([]);
    expect(mockSuggestions({ ...input, next: null })).toEqual([]);
    expect(mockSuggestions({ ...input, next: { ...input.next, is_user: false } })).toEqual([]);
  });

  it("uses the shared snake clock presentation for active, paused, and complete mocks", () => {
    const teams = [
      { id: 1, name: "Brian", draft_slot: 0, is_user: true },
      { id: 2, name: "CPU 2", draft_slot: 1, is_user: false },
    ];
    const next = { overall_pick: 2, round: 1, round_pick: 2, team_id: 2, team_name: "CPU 2", is_user: false, direction: "forward" as const };

    expect(mockClockState("active", teams, 2, next)).toEqual({
      presentation: { current: "Rd 1 P2 · CPU 2", next: "Next: CPU 2", accessible: "Round 1, pick 2. CPU 2. Next: CPU 2" },
      summary: "Round 1 of 2 · Pick 2 of 4 · CPU 2 is on the clock",
    });
    expect(mockClockState("paused", teams, 2, next).summary).toMatch(/^Paused/);
    expect(mockClockState("complete", teams, 2, null)).toEqual({ presentation: null, summary: "Draft complete" });
  });

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
