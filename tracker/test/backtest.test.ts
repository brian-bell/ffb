import { describe, expect, it } from "vitest";
import { backtestDraft, boardRankRanker, liveLineupRanker, marketRanker, type Ranker } from "../src/backtest";
import { nextPick } from "../src/draft";
import type { DraftState } from "../src/draft-store";
import type { Board, Player } from "../src/types";
import fixture from "./fixtures/board.json";

function player(key: string, pos: string, points: number, rank: number, adp = rank): Player {
  return { ...fixture.players[0]!, key, name: key, pos, points, rank, adp, adp_rank: adp, bye: null, injury: undefined } as Player;
}

const teams = [
  { id: 1, name: "Other", draft_slot: 0, is_user: false },
  { id: 2, name: "Brian", draft_slot: 1, is_user: true },
];
// Slots: one RB and one WR starter, one bench. Board rank deliberately puts a
// second RB ahead of the WR so board order and lineup gain disagree.
const board: Board = {
  ...fixture, num_teams: 2, roster_slots: { RB: 1, WR: 1, BN: 1 },
  players: [
    player("rb1", "RB", 300, 1), player("rb2", "RB", 250, 2), player("rb3", "RB", 200, 3),
    player("wr1", "WR", 180, 4), player("wr2", "WR", 150, 5), player("wr3", "WR", 100, 6),
  ],
} as Board;
// Snake: Other rb1; user rb2, rb3 (a mistake: wr1 was open); Other wr1, wr2; user wr3.
const order = ["rb1", "rb2", "rb3", "wr1", "wr2", "wr3"];
const saved: DraftState = {
  configured: true, draft: { name: "Saved", rounds: 3, team_count: 2 }, teams,
  complete: true, revision: 6, next: null,
  picks: order.map((key, i) => ({
    ...nextPick(teams, 3, i + 1)!, player_key: key, player_name: key,
    player_pos: board.players.find(p => p.key === key)!.pos, player_team: "BUF", picked_at: "2026-09-06",
  })),
};

describe("draft recommendation backtest", () => {
  it("scores the recorded roster and holds opponents' picks fixed while following a ranker", () => {
    const before = JSON.stringify(saved);
    const report = backtestDraft(saved, board, [boardRankRanker, liveLineupRanker]);
    expect(report.user).toBe("Brian");
    // Actual roster rb2, rb3, wr3: starters rb2 + wr3.
    expect(report.actualTotal).toBe(350);
    expect(report.actualRoster.map(p => p.key)).toEqual(["rb2", "rb3", "wr3"]);
    const byName = new Map(report.rankers.map(r => [r.ranker, r]));
    // Board rank follows the same mistake: rb2, rb3, then wr3 remain.
    expect(byName.get("board")!.followedRoster.map(p => p.key)).toEqual(["rb2", "rb3", "wr3"]);
    expect(byName.get("board")!.followedTotal).toBe(350);
    // Lineup gain takes rb2, then wr1 (180 gain) over rb3 (0 gain); Other's wr1 vanishes; then rb3 by rank.
    const live = byName.get("live")!;
    expect(live.followedRoster.map(p => p.key)).toEqual(["rb2", "wr1", "rb3"]);
    expect(live.followedTotal).toBe(430);
    expect(JSON.stringify(saved)).toBe(before);
  });
  it("compares each recorded own pick with the recommendation at that turn", () => {
    const live = backtestDraft(saved, board, [liveLineupRanker]).rankers[0]!;
    expect(live.picks.map(p => [p.overall_pick, p.actual?.key, p.recommended?.key, p.agreed])).toEqual([
      [2, "rb2", "rb2", true], [3, "rb3", "wr1", false], [6, "wr3", "wr3", true],
    ]);
    expect(live.picks[1]).toMatchObject({ actualGain: 0, recommendedGain: 180 });
    expect(live.agreement).toBeCloseTo(2 / 3);
  });
  it("removes a player from a later opponent pick when the followed ranker already took them", () => {
    const greedyWr: Ranker = { name: "wr-first", order: ({ available }) => [...available].sort((a, b) => (a.pos === "WR" ? 0 : 1) - (b.pos === "WR" ? 0 : 1) || a.rank - b.rank) };
    const result = backtestDraft(saved, board, [greedyWr]).rankers[0]!;
    // Takes wr1 and wr2 at picks 2-3; Other's recorded wr1 and wr2 vanish rather than displacing the user.
    expect(result.followedRoster.map(p => p.key)).toEqual(["wr1", "wr2", "wr3"]);
    expect(result.followedTotal).toBe(180);
  });
  it("is deterministic and orders the market ranker by ADP", () => {
    const a = backtestDraft(saved, board, [marketRanker, boardRankRanker, liveLineupRanker]);
    const b = backtestDraft(saved, board, [marketRanker, boardRankRanker, liveLineupRanker]);
    expect(JSON.stringify(a)).toBe(JSON.stringify(b));
    expect(a.rankers[0]!.picks[0]!.recommended?.key).toBe("rb2");
  });
  it("rejects a draft without a user team or unconfigured state", () => {
    expect(() => backtestDraft({ configured: false, picks: [], revision: 0 }, board, [])).toThrow();
    expect(() => backtestDraft({ ...saved, teams: teams.map(t => ({ ...t, is_user: false })) }, board, [])).toThrow();
  });
});
