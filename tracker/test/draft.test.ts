import { describe, expect, it } from "vitest";
import { nextPick } from "../src/draft";

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
