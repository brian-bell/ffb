import { describe, expect, it } from "vitest";
import { renderBoard } from "../src/render";
import { expectedBestAtNextPick, liveStarterPriority, lineupPriorityOrder, survivalProbability } from "../src/starter-priority";
import type { Board, Player } from "../src/types";
import fixture from "./fixtures/board.json";
import type { DraftState } from "../src/draft-store";

function player(key: string, pos: string, points: number | null, rank = 1): Player {
  return { ...fixture.players[0]!, key, name: key, pos, points, rank } as Player;
}
function state(players: Player[]): DraftState {
  return { configured: true, revision: 0,
    teams: [{ id: 1, name: "Brian", draft_slot: 0, is_user: true }],
    picks: players.map((p, i) => ({ overall_pick: i + 1, round: 1, round_pick: i + 1,
      team_id: 1, team_name: "Brian", player_key: p.key, player_name: p.name,
      player_pos: p.pos, player_team: p.team, picked_at: "" })) };
}
function setup(owned: Player[], candidates: Player[], slots: Record<string, number> = { WR: 1, TE: 1, "W/R/T": 1, BN: 3 }) {
  const board = { ...fixture, roster_slots: slots, players: [...owned, ...candidates] } as Board;
  const draft = state(owned);
  const priority = liveStarterPriority(draft, board)!;
  return { board, draft, priority };
}
function order(owned: Player[], candidates: Player[], slots?: Record<string, number>) {
  const { board, priority } = setup(owned, candidates, slots);
  const html = renderBoard({ ...board, players: candidates }, "ALL", { lineupPriority: priority.candidates, byeConflicts: priority.byeConflicts });
  return [...html.matchAll(/<b>(.*?)<\/b>/g)].map(match => match[1]);
}

describe("live lineup priority", () => {
  it("ranks first WR ahead of second TE by usable gain despite static rank", () => {
    expect(order([player("owned-te", "TE", 200)], [player("second-te", "TE", 120, 1), player("first-wr", "WR", 180, 80)]))
      .toEqual(["first-wr", "second-te"]);
  });
  it("allows second TE to improve flex and an exceptional first TE to lead", () => {
    const owned = [player("te", "TE", 200), player("wr", "WR", 180), player("rb", "RB", 80)];
    expect(setup(owned, [player("upgrade", "TE", 150)]).priority.candidates.get("upgrade"))
      .toMatchObject({ gain: 70, reason: expect.stringContaining("flex") });
    expect(order([], [player("wr", "WR", 180), player("te", "TE", 260)])).toEqual(["te", "wr"]);
  });
  it("matches overlapping flex exactly without double counting", () => {
    const slots = { "W/T": 1, "W/R/T": 1, BN: 2 };
    const { priority } = setup([player("wr", "WR", 100)], [player("rb", "RB", 90)], slots);
    expect(priority.summary).toContain("W/R/T 1");
    expect(priority.candidates.get("rb")?.gain).toBe(90);
    expect(setup([player("wr", "WR", 100), player("rb", "RB", 90)], [player("te", "TE", 110)], slots).priority.candidates.get("te")?.gain).toBe(20);
  });
  it("uses alternate dedicated slots including QB and K", () => {
    expect(order([player("qb", "QB", 300)], [player("qb2", "QB", 250), player("k", "K", 100)], { QB: 2, K: 1, BN: 1 })).toEqual(["qb2", "k"]);
  });
  it("labels missing projections without inventing a numeric gain", () => {
    expect(setup([], [player("unknown", "WR", null)]).priority.candidates.get("unknown"))
      .toMatchObject({ gain: null, reason: expect.stringContaining("projection unavailable") });
    const { priority } = setup([player("owned", "TE", null)], [player("candidate", "TE", 150)]);
    expect(priority.candidates.get("candidate")?.gain).toBeNull();
    expect(priority.summary).toContain("projections incomplete");
  });
  it("recomputes gain and needs after picks and undo", () => {
    const wr = player("wr", "WR", 180), te = player("te", "TE", 100);
    const { board, draft } = setup([], [wr, te]);
    const before = liveStarterPriority(draft, board)!;
    draft.picks = state([wr]).picks;
    expect(liveStarterPriority(draft, board)!.summary).not.toBe(before.summary);
    draft.picks = [];
    expect(liveStarterPriority(draft, board)).toEqual(before);
  });
  it("uses bye penalties after lineup gain and keeps unknown byes neutral", () => {
    const owned = { ...player("owned", "WR", 200), bye: 8 };
    const clash = { ...player("clash", "WR", 150, 1), bye: 8 };
    const clear = { ...player("clear", "WR", 150, 2), bye: null };
    expect(order([owned], [clash, clear])).toEqual(["clear", "clash"]);
    expect(order([owned], [{ ...clash, points: 151 }, clear])).toEqual(["clash", "clear"]);
  });
  it("counts only unique own identities, normalizes DST and preserves input", () => {
    const { board, draft } = setup([player("def", "DEF", 100)], [], { DEF: 1, QB: 1, BN: 2 });
    draft.picks[0]!.player_pos = "DST";
    draft.picks.push({ ...draft.picks[0]!, overall_pick: 2 });
    draft.picks.push({ ...state([player("qb", "QB", 200)]).picks[0]!, team_id: 2 });
    const original = JSON.stringify(draft);
    expect(liveStarterPriority(draft, board)!.summary).toContain("QB 1");
    expect(liveStarterPriority(draft, board)!.summary).not.toContain("DEF 1");
    expect(JSON.stringify(draft)).toBe(original);
  });
  it("does not assume a roster before setup or without a board", () => {
    expect(liveStarterPriority(null)).toBeNull();
    expect(liveStarterPriority(state([]))).toBeNull();
  });
  it("retains occupancy for a saved pick missing from a republished board", () => {
    const owned = player("missing-te", "TE", 200);
    const { board } = setup([], [player("wr", "WR", 150)]);
    const priority = liveStarterPriority(state([owned]), board)!;
    expect(priority.summary).not.toContain("TE 1");
    expect(priority.summary).toContain("roster projections incomplete");
    expect(priority.candidates.get("wr")).toMatchObject({ gain: null, score: null, need: 0 });
  });
  it("preserves search, history, tier boundaries and the original scarcity values", () => {
    const low = { ...player("low", "WR", 100, 1), tier: 1, vorp: 44 };
    const high = { ...player("high", "WR", 200, 20), tier: 2, vorp: 33 };
    const { board, priority } = setup([], [low, high]);
    const options = { lineupPriority: priority.candidates };
    const sorted = renderBoard(board, "ALL", options);
    expect(sorted.indexOf("<b>high")).toBeLessThan(sorted.indexOf("<b>low"));
    expect(sorted).toContain("WR starter · +200.0 lineup pts");
    expect(sorted).toContain("44.0");
    const positional = renderBoard(board, "WR", options);
    expect(positional.indexOf("<b>low")).toBeLessThan(positional.indexOf("<b>high"));
    expect(positional.match(/data-tier-key=/g)).toHaveLength(2);
    const searched = renderBoard(board, "ALL", { ...options, searchResults: [low, high] });
    expect(searched.indexOf("<b>low")).toBeLessThan(searched.indexOf("<b>high"));
    expect(searched).not.toContain("lineup-reason");
    const picked = new Map([["low", { overall_pick: 2, round: 1, round_pick: 2, team_name: "Brian" }],
      ["high", { overall_pick: 1, round: 1, round_pick: 1, team_name: "Brian" }]]);
    const history = renderBoard(board, "ALL", { ...options, mode: "drafted", picked });
    expect(history.indexOf("<b>low")).toBeLessThan(history.indexOf("<b>high"));
    expect(history).not.toContain("lineup-reason");
  });
});

// Two-team snake, user in slot 0: own picks 1, 4, 5, 8.
const teams = [
  { id: 1, name: "Brian", draft_slot: 0, is_user: true },
  { id: 2, name: "Other", draft_slot: 1, is_user: false },
];
function withAdp(p: Player, adp: number | null, adp_stdev: number | null = 0.5): Player {
  return { ...p, adp, adp_stdev };
}
function draftAt(owned: Player[], current: number, rounds = 4): DraftState {
  const draft = state(owned);
  return { ...draft, teams, draft: { name: "Live", rounds, team_count: 2 },
    next: { overall_pick: current, round: 1, round_pick: 1, team_id: 1, team_name: "Brian", is_user: true, direction: "forward" } };
}
function ranked(owned: Player[], candidates: Player[], slots: Record<string, number>, current = owned.length + 1, rounds = 4) {
  const board = { ...fixture, roster_slots: slots, players: [...owned, ...candidates] } as Board;
  const priority = liveStarterPriority(draftAt(owned, current, rounds), board)!;
  const keys = [...candidates].sort(lineupPriorityOrder(priority.candidates, priority.byeConflicts)).map(p => p.key);
  return { priority, keys };
}

describe("opportunity-cost live ranking", () => {
  it("survival follows ADP spread against the next own pick", () => {
    expect(survivalProbability(10, 2, 10)).toBeCloseTo(0.5);
    expect(survivalProbability(5, 1, 4)).toBeCloseTo(0.8413, 3);
    expect(survivalProbability(null, null, 5)).toBe(1);
    // Missing spread falls back to max(3, 10% of ADP).
    expect(survivalProbability(50, null, 55)).toBeCloseTo(0.1587, 3);
    expect(survivalProbability(10, 0, 13)).toBeCloseTo(0.1587, 3);
  });
  it("expected best alternative weights each value by reaching it", () => {
    expect(expectedBestAtNextPick([{ value: 100, survival: 0.5 }, { value: 60, survival: 1 }])).toBeCloseTo(80);
    expect(expectedBestAtNextPick([{ value: 60, survival: 1 }, { value: 100, survival: 0.5 }])).toBeCloseTo(80);
    expect(expectedBestAtNextPick([])).toBe(0);
  });
  it("prefers the RB that will not last over a QB whose replacement will", () => {
    const slots = { QB: 1, RB: 1, BN: 2 };
    const { keys, priority } = ranked([], [
      withAdp(player("qb-a", "QB", 330, 1), 30), withAdp(player("qb-b", "QB", 320, 2), 60),
      withAdp(player("rb-a", "RB", 200, 3), 2), withAdp(player("rb-b", "RB", 190, 4), 1.5),
    ], slots);
    expect(keys.slice(0, 2)).toEqual(["rb-a", "rb-b"]);
    // QB A will last, so waiting costs nothing.
    expect(priority.candidates.get("qb-a")!.score).toBeCloseTo(0, 0);
    expect(priority.candidates.get("rb-a")!.reason).toBe("RB starter · +200.0 lineup pts · likely gone by your next pick");
    expect(priority.candidates.get("qb-b")!.reason).toContain("should last to your next pick");
    expect(ranked([], [withAdp(player("coin", "RB", 100), 4, 2)], slots).priority.candidates.get("coin")!.reason)
      .toContain("coin flip to last");
  });
  it("counts the candidate's own survival so a big drop-off that will last is not urgent", () => {
    const slots = { QB: 1, RB: 1, BN: 2 };
    const { keys, priority } = ranked([], [
      withAdp(player("qb-a", "QB", 330, 1), 30), withAdp(player("qb-b", "QB", 200, 2), 60),
      withAdp(player("rb-a", "RB", 200, 3), 3.5), withAdp(player("rb-b", "RB", 150, 4), 40),
    ], slots);
    // RB A: 200 − (0.16×200 + 0.84×150) ≈ 42; QB A: 330 − 330 = 0.
    expect(priority.candidates.get("rb-a")!.score).toBeCloseTo(42, 0);
    expect(priority.candidates.get("qb-a")!.score).toBe(0);
    expect(keys[0]).toBe("rb-a");
  });
  it("has no opportunity cost or survival phrase at the last own pick", () => {
    const slots = { QB: 1, BN: 1 };
    const { priority } = ranked([], [withAdp(player("qb-a", "QB", 330), 30), withAdp(player("qb-b", "QB", 320), 60)], slots, 8);
    expect(priority.candidates.get("qb-a")).toMatchObject({ score: 330, survival: null, reason: "QB starter · +330.0 lineup pts" });
  });
  it("gives discounted depth value so a contested RB outranks a second DEF", () => {
    const owned = [player("qb", "QB", 300), player("rb", "RB", 250), player("def", "DEF", 120)];
    const slots = { QB: 1, RB: 1, DEF: 1, BN: 4 };
    const { keys, priority } = ranked(owned, [
      withAdp(player("def2", "DEF", 110, 1), null), withAdp(player("rb4", "RB", 200, 50), 4.5),
    ], slots, 4);
    expect(keys).toEqual(["rb4", "def2"]);
    expect(priority.candidates.get("rb4")!.reason).toMatch(/^RB depth · \+50\.0 \(bench\) · likely gone/);
    // An empty DEF slot with a real drop-off to the next DEF still leads.
    const first = ranked([player("qb", "QB", 300), player("rb", "RB", 250)], [
      withAdp(player("def1", "DEF", 140, 60), 3), withAdp(player("def-late", "DEF", 60, 70), null),
      withAdp(player("rb4", "RB", 200, 50), null), withAdp(player("rb5", "RB", 190, 51), null),
    ], slots, 4);
    expect(first.keys[0]).toBe("def1");
  });
  it("gives depth no value once the bench is full and falls back to rank", () => {
    const owned = [player("rb", "RB", 250), player("bench", "RB", 150)];
    const { keys, priority } = ranked(owned, [player("wr", "WR", 180, 9), player("te", "TE", 100, 3)], { RB: 1, BN: 1 }, 4);
    expect(priority.candidates.get("wr")).toMatchObject({ score: 0, reason: "Depth · no projected lineup gain" });
    expect(keys).toEqual(["te", "wr"]);
  });
  it("breaks equal scores by bye clash then rank and puts unprojected rows last", () => {
    const owned = { ...player("owned", "WR", 200), bye: 8 };
    const slots = { WR: 2, BN: 1 };
    const clash = { ...player("clash", "WR", 150, 1), bye: 8 };
    const clear = { ...player("clear", "WR", 150.04, 2), bye: 9 };
    const unknown = player("unknown", "WR", null, 0);
    expect(ranked([owned], [unknown, clash, clear], slots, 8).keys).toEqual(["clear", "clash", "unknown"]);
  });
});
