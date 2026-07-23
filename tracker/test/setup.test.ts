import { describe, expect, it } from "vitest";
import { makeSetupStore, nextSetupDialog, setupValidation, teamOptionsFromSetup, teamsFromSetup, validateSetup } from "../src/setup";

describe("teamsFromSetup", () => {
  it("turns the guided newline form into ordered team input without blank slots", () => {
    expect(teamsFromSetup(" Brian \n\nTeam 1\nTeam 2 ", "Team 1")).toEqual([
      { name: "Brian", is_user: false },
      { name: "Team 1", is_user: true },
      { name: "Team 2", is_user: false },
    ]);
  });
});

describe("teamOptionsFromSetup", () => {
  it("preserves literal team names and the selected choice as plain data", () => {
    expect(teamOptionsFromSetup('Team "One"\n<script>\nBrian\'s Team', "Brian's Team")).toEqual([
      { name: 'Team "One"', selected: false },
      { name: "<script>", selected: false },
      { name: "Brian's Team", selected: true },
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

describe("nextSetupDialog", () => {
  it("closes setup after the draft is reset", () => {
    expect(nextSetupDialog(true, { type: "draftReset" })).toBe(false);
  });

  it("opens setup only when the user requests it", () => {
    expect(nextSetupDialog(false, { type: "close" })).toBe(false);
    expect(nextSetupDialog(false, { type: "open" })).toBe(true);
  });
});

describe("makeSetupStore", () => {
  it("remembers the last successful draft setup across page loads", () => {
    const values = new Map<string, string>();
    const storage = {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => void values.set(key, value),
    };
    const setup = {
      name: "Home league",
      rounds: 15,
      teams: [
        { name: "Brian", is_user: true },
        { name: "Opponent", is_user: false },
      ],
    };

    makeSetupStore(storage).set(setup);

    expect(makeSetupStore(storage).get()).toEqual(setup);
  });

  it("ignores incomplete stored setup instead of prefilling a broken form", () => {
    const storage = {
      getItem: () => JSON.stringify({
        name: "Broken",
        teams: [
          { name: "Brian", is_user: true },
          { name: "Opponent", is_user: false },
        ],
      }),
      setItem: () => undefined,
    };

    expect(makeSetupStore(storage).get()).toBeNull();
  });
});
