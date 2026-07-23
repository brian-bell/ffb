import { describe, expect, it } from "vitest";
import { draftClockLabel, draftClockPresentation, draftNextLabel, draftPageTitle, nextPick } from "../src/draft";

describe("nextPick", () => {
  it("derives the snake turn at a round boundary", () => {
    const teams = [
      { id: 1, name: "A", draft_slot: 0, is_user: false },
      { id: 2, name: "Brian", draft_slot: 1, is_user: true },
      { id: 3, name: "C", draft_slot: 2, is_user: false },
      { id: 4, name: "D", draft_slot: 3, is_user: false },
    ];

    expect(nextPick(teams, 2, 2)).toMatchObject({
      overall_pick: 2,
      round: 1,
      round_pick: 2,
      team_id: 2,
      team_name: "Brian",
      is_user: true,
      direction: "forward",
    });
    expect(nextPick(teams, 2, 5)).toMatchObject({
      overall_pick: 5,
      round: 2,
      round_pick: 1,
      team_id: 4,
      team_name: "D",
      direction: "reverse",
    });
  });
});

describe("draft clock presentation", () => {
  it("shows round, round pick, and a team name capped at nine characters", () => {
    expect(draftClockLabel({ round: 1, round_pick: 5, team_name: "Team Name" })).toBe("Rd 1 P5 · Team Name");
    expect(draftClockLabel({ round: 2, round_pick: 1, team_name: "Longer Team" })).toBe("Rd 2 P1 · Longer...");
  });

  it("shows the next team with the same name cap and marks the end of the draft", () => {
    expect(draftNextLabel("Team Name")).toBe("Next: Team Name");
    expect(draftNextLabel("Longer Team")).toBe("Next: Longer...");
    expect(draftNextLabel(null)).toBe("Next: Done");
  });

  it("keeps complete current and next team names in its accessible summary", () => {
    expect(draftClockPresentation({ round: 2, round_pick: 1, team_name: "Longer Team" }, "Another Long Team")).toEqual({
      current: "Rd 2 P1 · Longer...",
      next: "Next: Anothe...",
      accessible: "Round 2, pick 1. Longer Team. Next: Another Long Team",
    });
  });

  it("uses the draft name only while a draft is in progress", () => {
    expect(draftPageTitle("Home League", true)).toBe("Home League");
    expect(draftPageTitle("Home League", false)).toBe("Draft Room");
    expect(draftPageTitle(null, false)).toBe("Draft Room");
  });
});
