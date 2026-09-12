import { describe, expect, it } from "vitest";
import { replayState, nextOwnPickCursor } from "../src/draft-replay";
import { nextPick } from "../src/draft";
import type { DraftState } from "../src/draft-store";

const teams = [
  { id: 1, name: "Other", draft_slot: 0, is_user: false },
  { id: 2, name: "Brian", draft_slot: 1, is_user: true },
];
const saved: DraftState = {
  configured: true, draft: { name: "Saved", rounds: 2, team_count: 2 }, teams,
  complete: true, revision: 4, next: null,
  picks: Array.from({ length: 4 }, (_, i) => ({
    ...nextPick(teams, 2, i + 1)!, player_key: `p${i}`, player_name: `Player ${i}`,
    player_pos: "WR", player_team: "BUF", picked_at: "2026-09-06",
  })),
};

describe("saved draft replay", () => {
  it("reconstructs a prefix and snake clock without changing the archive", () => {
    const before = JSON.stringify(saved);
    expect(replayState(saved, 0).picks).toEqual([]);
    const state = replayState(saved, 2);
    expect(state.picks.map(p => p.player_key)).toEqual(["p0", "p1"]);
    expect(state.next).toMatchObject({ overall_pick: 3, team_id: 2, round: 2 });
    expect(state.complete).toBe(false);
    expect(replayState(saved, 4).complete).toBe(true);
    expect(replayState(saved, 1).picks).toHaveLength(1);
    expect(JSON.stringify(saved)).toBe(before);
  });
  it("jumps to immediately before the next own pick, including consecutive snake turns", () => {
    expect(nextOwnPickCursor(saved, 0)).toBe(1);
    expect(nextOwnPickCursor(saved, 1)).toBe(2);
    expect(nextOwnPickCursor(saved, 2)).toBe(4);
  });
  it("rejects invalid cursor positions instead of silently showing a wrong board", () => {
    for (const cursor of [-1, 5, NaN, 1.5]) expect(() => replayState(saved, cursor)).toThrow();
  });
});

import fixture from "./fixtures/board.json";
import type { Board } from "../src/types";
import { buildPlayerPool } from "../src/player-pool";
import { liveStarterPriority } from "../src/starter-priority";

it("removes replayed players and recomputes lineup gains, restoring both on rewind", () => {
  const board = { ...fixture, roster_slots: { QB: 1, BN: 2 } } as Board;
  const qb = board.players.find(p => p.pos === "QB")!;
  const source = { ...saved, picks: saved.picks.map((p, i) => i === 1 ? {
    ...p, player_key: qb.key, player_name: qb.name, player_pos: qb.pos, player_team: qb.team,
  } : p) };
  const before = replayState(source, 1);
  const after = replayState(source, 2);
  expect(buildPlayerPool(board.players, before.picks).available.some(p => p.key === qb.key)).toBe(true);
  expect(buildPlayerPool(board.players, after.picks).available.some(p => p.key === qb.key)).toBe(false);
  expect(liveStarterPriority(before, board)!.candidates.get(qb.key)!.gain).toBe(qb.points);
  expect(liveStarterPriority(after, board)!.candidates.get(qb.key)!.gain).toBe(0);
  expect(replayState(source, 1)).toEqual(before);
});

import { parseReplayDraft, readReplaySession, REPLAY_STORAGE_KEY } from "../src/draft-replay";

it("validates archives before replay and rejects corrupt session state", () => {
  expect(parseReplayDraft(saved).picks).toEqual(saved.picks);
  const invalid = [null, {}, { ...saved, teams: [] },
    { ...saved, picks: [...saved.picks].reverse() },
    { ...saved, picks: saved.picks.map(p => ({ ...p, player_key: "duplicate" })) },
    { ...saved, picks: saved.picks.map(p => ({ ...p, team_id: 99 })) }];
  for (const value of invalid) expect(() => parseReplayDraft(value)).toThrow();
  expect(readReplaySession(null)).toBeNull();
  expect(readReplaySession({ getItem: () => "{" })).toBeNull();
  expect(readReplaySession({ getItem: () => JSON.stringify({ saved, cursor: 99 }) })).toBeNull();
  expect(readReplaySession({ getItem: key => {
    expect(key).toBe(REPLAY_STORAGE_KEY);
    return JSON.stringify({ saved, cursor: 2 });
  } })?.cursor).toBe(2);
});

import completedMcfflDraft from "../../drafts/mcffl-2026.json";

it("can replay the preserved MCFFL draft from start through all 150 picks", () => {
  const saved = parseReplayDraft(completedMcfflDraft);
  expect(saved.teams).toHaveLength(10);
  expect(saved.picks).toHaveLength(150);
  expect(saved.picks[0]?.player_name).toBe("Jahmyr Gibbs");
  expect(saved.picks.at(-1)?.player_name).toBe("Kenneth Gainwell");
  expect(replayState(saved, 0).picks).toHaveLength(0);
  expect(replayState(saved, 150).complete).toBe(true);
});
