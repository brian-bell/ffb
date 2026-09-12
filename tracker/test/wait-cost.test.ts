import { describe, expect, it } from "vitest";
import { nextUserTurn, marketAvailability, waitingCost, mockWaitingCost } from "../src/wait-cost";
import { nextPick } from "../src/draft";
import type { DraftState } from "../src/draft-store";
import type { Board, Player } from "../src/types";
import fixture from "./fixtures/board.json";
const teams = Array.from({ length: 10 }, (_, i) => ({ id: i + 1, name: `${i}`, draft_slot: i, is_user: i === 0 }));
function player(key: string, pos: string, points: number | null, adp: number | null): Player {
  return { ...fixture.players[0], key, name: key, pos, points, adp, adp_high: null, adp_low: null, adp_stdev: null } as Player;
}
function advice(players: Player[], key = players[0]!.key) {
  const board = { ...fixture, roster_slots: { TE: 1, WR: 1, BN: 3 }, players } as Board;
  const draft: DraftState = { configured: true, revision: 0, teams, draft: { name: "Test", rounds: 4, team_count: 10 }, picks: [], next: nextPick(teams, 4, 1) };
  return waitingCost(draft, board, key)!;
}
describe("waiting cost", () => {
  it("counts snake wrap and consecutive turns and stops at draft end", () => {
    expect(nextUserTurn(teams, 4, 1)).toEqual({ pick: 20, opponents: 18 });
    expect(nextUserTurn(teams, 4, 20)).toEqual({ pick: 21, opponents: 0 });
    expect(nextUserTurn(teams, 4, 40)).toBeNull();
    const end = teams.map(t => ({ ...t, is_user: t.draft_slot === 9 }));
    expect(nextUserTurn(end, 4, 10)).toEqual({ pick: 11, opponents: 0 });
  });
  it("uses wide dispersion conservatively and never invents probabilities", () => {
    const p = player("te", "TE", 140, 3);
    expect(marketAvailability(p, 20, 10)).toBe("at risk");
    expect(marketAvailability({ ...p, adp_low: 30 }, 20, 10)).toBe("uncertain");
    expect(marketAvailability({ ...p, adp_stdev: 25 }, 20, 10)).toBe("uncertain");
    expect(marketAvailability({ ...p, adp: null }, 20, 10)).toBe("unknown");
  });
  it("explains a scarce lower-point TE with greater positional loss", () => {
    const players = [player("te", "TE", 140, 3), player("later-te", "TE", 60, 60), player("wr", "WR", 180, 4), player("later-wr", "WR", 170, 65)];
    expect(advice(players)).toMatchObject({ gain: 140, dropOff: 80, availability: "at risk", alternative: "later-te" });
    expect(advice(players, "wr").dropOff).toBe(10);
    expect(advice(players).text).toContain("80.0 lineup pts");
    expect(advice(players).text).toContain("not a probability");
  });
  it("treats a player with later ADP as a possible wait, not guaranteed", () => {
    const result = advice([player("later", "TE", 140, 60)]);
    expect(result).toMatchObject({ availability: "reasonable later", dropOff: 0 });
    expect(result.text).toContain("Waiting is plausible");
  });
  it("does not turn absent alternatives or missing inputs into certain loss", () => {
    expect(advice([player("te", "TE", 140, 3)]).dropOff).toBeNull();
    expect(advice([player("te", "TE", null, 3)]).gain).toBeNull();
    expect(advice([player("te", "TE", 140, null)]).availability).toBe("unknown");
    expect(advice([player("te", "TE", 140, 3), player("later", "TE", null, 60)]).dropOff).toBeNull();
  });
});


describe("waiting advice integration", () => {
  const te = player("te", "TE", 140, 3);
  const later = player("later", "TE", 60, 60);
  const board = { ...fixture, roster_slots: { TE: 1, BN: 3 }, players: [te, later] } as Board;
  const draft: DraftState = { configured: true, revision: 0, teams, draft: { name: "Test", rounds: 4, team_count: 10 }, picks: [], next: nextPick(teams, 4, 1) };
  const pick = { overall_pick: 1, round: 1, round_pick: 1, team_id: 1, team_name: "0", player_key: "later", player_name: "later", player_pos: "TE", player_team: later.team, picked_at: "" };
  it("excludes drafted alternatives and candidate identities", () => {
    const state = { ...draft, picks: [{ ...pick, team_id: 2 }] };
    expect(waitingCost(state, board, "te")?.dropOff).toBeNull();
    expect(waitingCost(state, board, "later")).toBeNull();
  });
  it("uses owned starter contribution and recovers after undo", () => {
    const state = { ...draft, picks: [pick] };
    expect(waitingCost(state, board, "te")?.gain).toBe(80);
    expect(waitingCost({ ...state, picks: [] }, board, "te")?.gain).toBe(140);
    const incomplete = { ...board, players: [te, { ...later, points: null }] };
    expect(waitingCost(state, incomplete, "te")?.gain).toBeNull();
  });
  it("does not recommend depth solely on scarcity or advise on opponent turns", () => {
    const strong = { ...later, points: 200 };
    const result = waitingCost({ ...draft, picks: [pick] }, { ...board, players: [te, strong] }, "te");
    expect(result?.gain).toBe(0);
    expect(result?.text).toContain("scarcity alone does not justify");
    expect(waitingCost({ ...draft, next: nextPick(teams, 4, 2) }, board, "te")).toBeNull();
  });
  it("uses identical advice for mock state without changing its seeded state", () => {
    const mock = { configured: true, revision: 0, teams, picks: [{ ...pick, draft_slot: 0, source: "user" as const }], next: draft.next, lifecycle: "active" as const,
      mock: { id: "m", board_fingerprint: "b", seed: 42, strategy_version: "market-need-v1", user_slot: 1, team_count: 10, rounds: 4, variance_preset: "realistic" as const } };
    const before = JSON.stringify(mock);
    expect(mockWaitingCost(mock, board, "te")).toEqual(waitingCost({ ...draft, picks: [pick] }, board, "te"));
    expect(JSON.stringify(mock)).toBe(before);
    expect(mockWaitingCost({ ...mock, lifecycle: "paused" }, board, "te")).toBeNull();
  });
});
