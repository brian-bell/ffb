import { describe, expect, it } from "vitest";
import { setupValidation, teamsFromSetup, validateSetup } from "../src/setup";

describe("teamsFromSetup", () => {
  it("turns the guided newline form into ordered team input without blank slots", () => {
    expect(teamsFromSetup(" Brian \n\nTeam 1\nTeam 2 ", "Team 1")).toEqual([
      { name: "Brian", is_user: false },
      { name: "Team 1", is_user: true },
      { name: "Team 2", is_user: false },
    ]);
  });
});

describe("validateSetup", () => {
  it("explains incomplete setup before making a request", () => {
    expect(validateSetup("Brian", "Brian")).toMatch(/at least two/i);
    expect(validateSetup("Brian\nTeam 1", "")).toMatch(/choose/i);
    expect(validateSetup("Brian\n brian ", "Brian")).toMatch(/unique/i);
  });

  it("accepts two distinct ordered teams and a selected owner", () => {
    expect(validateSetup("Brian\nTeam 1", "Brian")).toBeNull();
  });

  it("identifies the field that needs attention", () => {
    expect(setupValidation("Brian", "Brian")).toMatchObject({ field: "teams" });
    expect(setupValidation("Brian\nTeam 1", "")).toMatchObject({ field: "user_team" });
    expect(setupValidation("Brian\nTeam 1", "Brian", 0)).toMatchObject({ field: "rounds" });
  });
});
