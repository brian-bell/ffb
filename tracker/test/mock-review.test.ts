import { describe, expect, it } from "vitest";
import type { MockPick, MockState } from "../src/mock-draft";
import {
  entersReview,
  mockReviewModel,
  renderMockReviewRosters,
  renderMockReviewSummary,
} from "../src/mock-review";

function pick(overrides: Partial<MockPick> & Pick<MockPick, "overall_pick" | "draft_slot">): MockPick {
  const round = Math.floor((overrides.overall_pick - 1) / 2) + 1;
  return {
    round,
    round_pick: ((overrides.overall_pick - 1) % 2) + 1,
    team_name: overrides.draft_slot === 1 ? "Brian" : "CPU 1",
    player_key: `p${overrides.overall_pick}`,
    player_name: `Player ${overrides.overall_pick}`,
    player_pos: "RB",
    player_team: "BUF",
    source: "simulated",
    ...overrides,
  };
}

const teams = [
  { id: 1, name: "CPU 1", draft_slot: 0, is_user: false },
  { id: 2, name: "Brian", draft_slot: 1, is_user: true },
];

const completedState: MockState = {
  configured: true,
  mock: {
    id: "mock-1",
    board_fingerprint: "abc123",
    seed: 8042,
    strategy_version: "market-need-v1",
    user_slot: 2,
    team_count: 2,
    rounds: 2,
    variance_preset: "realistic",
  },
  teams,
  picks: [
    pick({ overall_pick: 1, draft_slot: 0, player_pos: "QB", player_team: "NYJ" }),
    pick({ overall_pick: 2, draft_slot: 1, source: "user", player_pos: "RB" }),
    pick({ overall_pick: 3, draft_slot: 1, source: "user", player_pos: "WR", player_team: null }),
    pick({ overall_pick: 4, draft_slot: 0, player_pos: null }),
  ],
  next: null,
  complete: true,
  lifecycle: "complete",
  can_undo: true,
  revision: 4,
};

describe("mock review model", () => {
  it("returns null unless a configured mock is complete with a usable board", () => {
    expect(mockReviewModel({ configured: false, picks: [], revision: 0 })).toBeNull();
    expect(mockReviewModel({ ...completedState, lifecycle: "active", complete: false, next: null })).toBeNull();
    expect(mockReviewModel({ ...completedState, lifecycle: "paused", complete: false })).toBeNull();
    expect(mockReviewModel({ ...completedState, board_error: "saved board unavailable" })).toBeNull();
    expect(mockReviewModel(completedState)).not.toBeNull();
  });

  it("groups picks into per-team rosters with the user team first", () => {
    const model = mockReviewModel(completedState)!;

    expect(model.headline).toBe("Mock complete · 4 picks · 2 rounds · 2 teams");
    expect(model.rosters.map((roster) => roster.team_name)).toEqual(["Brian", "CPU 1"]);
    expect(model.user.team_name).toBe("Brian");
    expect(model.user.is_user).toBe(true);
    expect(model.user.picks.map((p) => p.overall_pick)).toEqual([2, 3]);
    expect(model.rosters[1]!.picks.map((p) => p.overall_pick)).toEqual([1, 4]);
  });

  it("counts positions in canonical order with a dash bucket for unknown positions", () => {
    const model = mockReviewModel(completedState)!;

    expect(model.user.position_counts).toEqual([
      { pos: "RB", count: 1 },
      { pos: "WR", count: 1 },
    ]);
    expect(model.rosters[1]!.position_counts).toEqual([
      { pos: "QB", count: 1 },
      { pos: "—", count: 1 },
    ]);
  });

  it("records the reproduction config verbatim", () => {
    const model = mockReviewModel(completedState)!;

    expect(model.config).toEqual({
      seed: 8042,
      variance_label: "Realistic",
      user_slot: 2,
      team_count: 2,
      rounds: 2,
      strategy_version: "market-need-v1",
      board_fingerprint: "abc123",
    });
  });
});

describe("entersReview", () => {
  const entry = (lifecycle: "active" | "paused" | "complete", mock_id = "mock-1") =>
    ({ lifecycle, mock_id });

  it("fires only when a state newly lands in review", () => {
    expect(entersReview(null, entry("complete"))).toBe(true);
    expect(entersReview(entry("active"), entry("complete"))).toBe(true);
    expect(entersReview(entry("paused"), entry("complete"))).toBe(true);
    expect(entersReview(entry("complete"), entry("complete"))).toBe(false);
    expect(entersReview(entry("complete"), entry("active"))).toBe(false);
    expect(entersReview(null, entry("active"))).toBe(false);
    expect(entersReview(null, null)).toBe(false);
  });

  it("treats a different completed mock as a fresh review entry", () => {
    expect(entersReview(entry("complete"), entry("complete", "mock-2"))).toBe(true);
    expect(entersReview(entry("active"), entry("complete", "mock-2"))).toBe(true);
  });
});

describe("review renderers", () => {
  it("summarizes the outcome with the user team and full reproduction config", () => {
    const html = renderMockReviewSummary(mockReviewModel(completedState)!);

    expect(html).toContain("Mock complete · 4 picks · 2 rounds · 2 teams");
    expect(html).toContain("Brian");
    expect(html).toContain("8042");
    expect(html).toContain("Realistic");
    expect(html).toContain("market-need-v1");
    expect(html).toContain("abc123");
    expect(html).toContain("Slot 2");
  });

  it("renders one accordion entry per team with the user roster open and marked", () => {
    const html = renderMockReviewRosters(mockReviewModel(completedState)!);

    expect(html.match(/<details/g)).toHaveLength(2);
    expect(html).toMatch(/<details[^>]*class="[^"]*user[^"]*"[^>]*open/);
    expect(html.match(/open/g)).toHaveLength(1);
    expect(html.indexOf("Brian")).toBeLessThan(html.indexOf("CPU 1"));
    expect(html).toContain("YOU");
    expect(html).toContain("1.02");
    expect(html).toContain("2.01");
    expect(html).toContain("Player 2");
    expect(html).toContain("RB · BUF");
    expect(html.match(/class="[^"]*you-pick/g)).toHaveLength(2);
    expect(html).toContain("RB 1 · WR 1");
  });

  it("escapes hostile player and team names", () => {
    const hostile: MockState = {
      ...completedState,
      teams: [
        { id: 1, name: '<img src=x onerror=alert(1)>', draft_slot: 0, is_user: false },
        teams[1]!,
      ],
      picks: [
        pick({ overall_pick: 1, draft_slot: 0, player_name: '<script>alert(1)</script>' }),
        ...completedState.picks.slice(1),
      ],
    };
    const model = mockReviewModel(hostile)!;
    const html = renderMockReviewSummary(model) + renderMockReviewRosters(model);

    expect(html).not.toContain("<script>");
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;script&gt;");
  });
});
