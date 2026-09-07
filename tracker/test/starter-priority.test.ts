import { describe, expect, it } from "vitest";
import { liveStarterPriority } from "../src/starter-priority";
import type { Board } from "../src/types";
import fixture from "./fixtures/board.json";
import type { DraftState } from "../src/draft-store";

function state(positions: (string | null)[]): DraftState {
  return {
    configured: true, revision: 0,
    teams: [
      { id: 1, name: "Brian", draft_slot: 0, is_user: true },
      { id: 2, name: "Other", draft_slot: 1, is_user: false },
    ],
    picks: positions.map((pos, i) => ({
      overall_pick: i + 1, round: 1, round_pick: i + 1, team_id: 1, team_name: "Brian",
      player_key: `p${i}`, player_name: `Player ${i}`, player_pos: pos, player_team: null, picked_at: "",
    })),
  };
}

describe("live starter priority", () => {
  it("uses the requested eight starter slots and no kicker", () => {
    const result = liveStarterPriority(state([]))!;
    expect([...result.positions]).toEqual(["QB", "RB", "WR", "TE", "DEF"]);
    expect(result.summary).toBe("Need: QB 1 · RB 2 · WR 1 · TE 1 · WR/TE 1 · FLEX 1 · DEF 1");
  });

  it("fills dedicated slots before flex and still prioritizes a missing tight end", () => {
    const result = liveStarterPriority(state(["QB", "RB", "RB", "RB", "WR", "WR", "DST"]))!;
    expect([...result.positions]).toEqual(["TE"]);
    expect(result.summary).toBe("Need: TE 1");
  });

  it("accepts surplus tight ends at flex but never surplus QBs or defenses", () => {
    const result = liveStarterPriority(state(["QB", "QB", "DEF", "DEF", "RB", "RB", "WR", "WR", "TE"]))!;
    expect([...result.positions]).toEqual(["RB", "WR", "TE"]);
    expect(result.summary).toBe("Need: FLEX 1");
    expect(liveStarterPriority(state(["QB", "RB", "RB", "WR", "WR", "TE", "TE", "DEF"]))!.positions.size).toBe(0);
  });

  it("allows a second tight end to fill WR/TE and reserves the broader flex for an RB", () => {
    const result = liveStarterPriority(state(["QB", "RB", "RB", "RB", "WR", "TE", "TE", "DEF"]))!;
    expect(result.positions.size).toBe(0);
    expect(result.summary).toBe("Starters filled · Building depth");
  });

  it("does not let an RB fill WR/TE or count one surplus receiver twice", () => {
    const onlyRestricted = liveStarterPriority(state(["QB", "RB", "RB", "RB", "WR", "TE", "DEF"]))!;
    expect([...onlyRestricted.positions]).toEqual(["WR", "TE"]);
    expect(onlyRestricted.summary).toBe("Need: WR/TE 1");
    const onlyBroad = liveStarterPriority(state(["QB", "RB", "RB", "WR", "TE", "TE", "DEF"]))!;
    expect([...onlyBroad.positions]).toEqual(["RB", "WR", "TE"]);
    expect(onlyBroad.summary).toBe("Need: FLEX 1");
  });

  it("uses only the user team and ignores unknown positions and duplicate players", () => {
    const draft = state(["QB", null, "RB"]);
    draft.picks[0]!.team_id = 2;
    draft.picks.push({ ...draft.picks[2]!, overall_pick: 4 });
    const original = JSON.stringify(draft);
    expect(liveStarterPriority(draft)!.summary).toBe("Need: QB 1 · RB 1 · WR 1 · TE 1 · WR/TE 1 · FLEX 1 · DEF 1");
    expect(JSON.stringify(draft)).toBe(original);
  });

  it("does not assume a user team before setup", () => {
    expect(liveStarterPriority(null)).toBeNull();
    expect(liveStarterPriority({ configured: false, revision: 0, picks: [] })).toBeNull();
    const draft = state([]);
    draft.teams = [];
    expect(liveStarterPriority(draft)).toBeNull();
  });
});


describe("bye conflicts", () => {
  it("counts own same-position overlaps, using board byes and conservative identities", () => {
    const board = fixture as unknown as Board;
    const draft = state(["RB", "WR"]);
    draft.picks[0] = { ...draft.picks[0]!, player_key: board.players[0]!.key, player_name: board.players[0]!.name, player_team: board.players[0]!.team };
    draft.picks[1]!.team_id = 2;
    const candidate = { ...board.players[0]!, key: "new-rb", name: "New RB" };
    const otherPosition = { ...candidate, key: "new-wr", name: "New WR", pos: "WR" };
    const unknown = { ...candidate, key: "unknown", bye: null };
    const otherWeek = { ...candidate, key: "other-week", bye: 10 };
    const result = liveStarterPriority(draft, { ...board, players: [...board.players, candidate, otherPosition, unknown, otherWeek] })!;
    expect(result.byeConflicts.get("new-rb")).toBe(1);
    expect(result.byeConflicts.has("new-wr")).toBe(false);
    expect(result.byeConflicts.has("unknown")).toBe(false);
    expect(result.byeConflicts.has("other-week")).toBe(false);
  });
});
