import { bench, describe } from "vitest";
import { buildPlayerPool, type PlayerPoolPick } from "../src/player-pool";
import type { Player } from "../src/types";

const PLAYER_COUNT = 2_131;
const PICK_COUNT = 200;
const players = Array.from({ length: PLAYER_COUNT }, (_, index) => ({
  key: `canonical:${index}`,
  name: `Player ${index}`,
  pos: index % 2 === 0 ? "WR" : "RB",
  team: "BUF",
  rank: index + 1,
  adp: index + 1,
  adp_rank: index + 1,
})) as Player[];
const picks: PlayerPoolPick[] = players.slice(0, PICK_COUNT).map((player, index) => ({
  overall_pick: index + 1,
  round: Math.floor(index / 20) + 1,
  round_pick: (index % 20) + 1,
  team_name: `Team ${(index % 20) + 1}`,
  player_key: player.key,
  player_name: player.name,
  player_pos: player.pos,
  player_team: player.team,
}));
const pool = buildPlayerPool(players, picks);

describe("production-sized player pool", () => {
  bench("builds availability for 2,131 players and 200 picks", () => {
    buildPlayerPool(players, picks);
  });

  bench("searches the cached available pool", () => {
    pool.search("player");
  });
});
