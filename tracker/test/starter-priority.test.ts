import { describe, expect, it } from "vitest";
import { renderBoard } from "../src/render";
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

const targets = ["QB", "QB", "RB", "RB", "WR", "WR", "WR", "WR", "TE", "TE", "DEF", "DEF"];

describe("live roster priority", () => {
  it("requires all twelve positional picks before either flex slot", () => {
    const result = liveStarterPriority(state([]))!;
    expect([...result.positions]).toEqual(["QB", "RB", "WR", "TE", "DEF"]);
    expect(result.summary).toBe("Need: QB 2 · RB 2 · WR 4 · TE 2 · DEF 2");
  });

  it("does not prioritize extra RB/WR/TE while any required position is short", () => {
    for (const pos of ["QB", "RB", "WR", "TE", "DEF"]) {
      const picks = [...targets];
      picks.splice(picks.indexOf(pos), 1);
      const result = liveStarterPriority(state(picks))!;
      expect([...result.positions]).toEqual([pos]);
      expect(result.summary).toBe(`Need: ${pos} 1`);
    }
  });

  it("opens flex priority once all positional targets are met", () => {
    const result = liveStarterPriority(state(targets))!;
    expect([...result.positions]).toEqual(["RB", "WR", "TE"]);
    expect(result.summary).toBe("Need: WR/TE 1 · FLEX 1");
    expect(liveStarterPriority(state([...targets, "TE"]))!.summary).toBe("Need: FLEX 1");
    expect([...liveStarterPriority(state([...targets, "TE", "RB"]))!.positions]).toEqual(["RB", "WR", "TE"]);
  });

  it("keeps offensive depth ahead of highly ranked third defenses and quarterbacks", () => {
    const draft = state([...targets, "TE", "RB"]);
    const board = { ...fixture, players: [
      { ...fixture.players[0]!, key: "extra-def", name: "Third defense", pos: "DEF", rank: 1 },
      { ...fixture.players[0]!, key: "extra-qb", name: "Third quarterback", pos: "QB", rank: 2 },
      { ...fixture.players[0]!, key: "depth-wr", name: "Depth receiver", pos: "WR", rank: 100 },
    ] } as unknown as Board;
    const priority = liveStarterPriority(draft, board)!;
    const html = renderBoard(board, "ALL", { starterPositions: priority.positions, byeConflicts: priority.byeConflicts });
    expect(priority.summary).toBe("Draft targets filled · Building depth");
    expect(html.indexOf("Depth receiver")).toBeLessThan(html.indexOf("Third defense"));
    expect(html.indexOf("Depth receiver")).toBeLessThan(html.indexOf("Third quarterback"));
    expect(html).toContain("Third defense");
  });

  it("reserves WR/TE before broad flex without counting surplus players twice", () => {
    const result = liveStarterPriority(state([...targets, "RB"]))!;
    expect([...result.positions]).toEqual(["WR", "TE"]);
    expect(result.summary).toBe("Need: WR/TE 1");
    expect(liveStarterPriority(state([...targets, "QB", "DEF"]))!.summary).toBe("Need: WR/TE 1 · FLEX 1");
  });

  it("keeps missing required slots first even when surplus flex players are already drafted", () => {
    const picks = [...targets, "RB", "TE"];
    picks.splice(picks.indexOf("QB"), 1);
    expect(liveStarterPriority(state(picks))!.summary).toBe("Need: QB 1");
    expect([...liveStarterPriority(state(picks))!.positions]).toEqual(["QB"]);
  });

  it("counts only unique user picks, normalizes DST, and does not mutate state", () => {
    const draft = state(["QB", null, "RB", "DST"]);
    draft.picks[0]!.team_id = 2;
    draft.picks.push({ ...draft.picks[2]!, overall_pick: 5 });
    const original = JSON.stringify(draft);
    expect(liveStarterPriority(draft)!.summary).toBe("Need: QB 2 · RB 1 · WR 4 · TE 2 · DEF 1");
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
