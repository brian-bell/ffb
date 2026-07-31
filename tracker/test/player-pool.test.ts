import { describe, expect, it } from "vitest";
import { buildPlayerPool, type PlayerPoolPick } from "../src/player-pool";
import type { Player } from "../src/types";

const players = [
  { key: "one", name: "Alpha One", pos: "RB", team: "BUF", rank: 2, adp: 4, adp_rank: 5 },
  { key: "two", name: "Alpha-Two", pos: "WR", team: "MIA", rank: 1, adp: 4, adp_rank: 4 },
  { key: "three", name: "Gamma Alpha", pos: "TE", team: "NYJ", rank: 3, adp: null, adp_rank: null },
] as Player[];

describe("player pool", () => {
  it("projects drafted annotations and searches only the remaining players", () => {
    const pool = buildPlayerPool(players, [
      {
        overall_pick: 1,
        round: 1,
        round_pick: 1,
        team_name: "Brian",
        player_key: "one",
        player_name: "Alpha One",
        player_pos: "RB",
        player_team: "BUF",
      },
    ]);

    expect(pool.picked.get("one")).toEqual({
      overall_pick: 1,
      round: 1,
      round_pick: 1,
      team_name: "Brian",
    });
    expect(pool.available.map((player) => player.key)).toEqual(["two", "three"]);
    expect(pool.search("alpha").map((player) => player.key)).toEqual(["two", "three"]);
  });

  it("preserves canonical, fallback, and defense identity equivalence", () => {
    const identityPlayers = [
      { key: "canonical:a-brown", name: "A. Brown", pos: "WR", team: "PHI", rank: 1 },
      { key: "manual:a-brown", name: "A Brown", pos: "WR", team: "PHI", rank: 2 },
      { key: "canonical:other", name: "A Brown", pos: "WR", team: "PHI", rank: 3 },
      { key: "def:SFO", name: "49ers D/ST", pos: "DEF", team: "SFO", rank: 4 },
      { key: "sleeper:sf", name: "San Francisco 49ers", pos: "DST", team: "SF", rank: 5 },
    ] as Player[];
    const pool = buildPlayerPool(identityPlayers, [
      {
        overall_pick: 1,
        round: 1,
        round_pick: 1,
        team_name: "Brian",
        player_key: "canonical:a-brown",
        player_name: "A. Brown",
        player_pos: "WR",
        player_team: "PHI",
      },
      {
        overall_pick: 2,
        round: 1,
        round_pick: 2,
        team_name: "Other",
        player_key: "sleeper:sf",
        player_name: "San Francisco 49ers",
        player_pos: "DST",
        player_team: "SF",
      },
    ]);

    expect([...pool.picked.keys()]).toEqual([
      "canonical:a-brown",
      "manual:a-brown",
      "def:SFO",
      "sleeper:sf",
    ]);
    expect(pool.available.map((player) => player.key)).toEqual(["canonical:other"]);
    expect(pool.search("a brown").map((player) => player.key)).toEqual(["canonical:other"]);
    expect(pool.search("49ers")).toEqual([]);
  });

  it("keeps search as a snapshot of the draft revision used to build it", () => {
    const picks: PlayerPoolPick[] = [];
    const pool = buildPlayerPool(players, picks);
    picks.push({
      overall_pick: 1,
      round: 1,
      round_pick: 1,
      team_name: "Brian",
      player_key: "one",
      player_name: "Alpha One",
      player_pos: "RB",
      player_team: "BUF",
    });

    expect(pool.search("alpha").map((player) => player.key)).toEqual(["two", "one", "three"]);
  });
});
