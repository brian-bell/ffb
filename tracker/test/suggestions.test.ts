import { describe, expect, it } from "vitest";
import { searchPlayers, suggestPlayers } from "../src/suggestions";
import type { Player } from "../src/types";

const players = [
  { key: "one", name: "Alpha One", pos: "RB", team: "BUF", rank: 2, adp: 4, adp_rank: 5 },
  { key: "two", name: "Alpha-Two", pos: "WR", team: "MIA", rank: 1, adp: 4, adp_rank: 4 },
  { key: "three", name: "Gamma Alpha", pos: "TE", team: "NYJ", rank: 3, adp: null, adp_rank: null },
  { key: "four", name: "Delta", pos: "QB", team: "KC", rank: 4, adp: null, adp_rank: null },
] as Player[];

describe("pick suggestions", () => {
  it("uses exact player keys to exclude picks and sorts market ADP before board fallback", () => {
    expect(suggestPlayers(players, new Set(["two"])).map((player) => player.key)).toEqual(["one", "three", "four"]);
  });

  it("searches available players by normalized prefix before token and substring matches", () => {
    expect(searchPlayers(players, new Set(["one"]), "alpha").map((player) => player.key)).toEqual(["two", "three"]);
    expect(searchPlayers(players, new Set(), "two").map((player) => player.key)).toEqual(["two"]);
  });

  it("hides equivalent drafted defense identities from both market suggestions and search", () => {
    const defenses = [
      { key: "sleeper:buf", name: "Buffalo Bills", pos: "DST", team: "BUF", rank: 1, adp: 1, adp_rank: 1 },
      { key: "ffc:buf", name: "Bills D/ST", pos: "DEF", team: "buf", rank: 2, adp: 2, adp_rank: 2 },
    ] as Player[];
    const picked = [{ key: "sleeper:buf", name: "Buffalo Bills", pos: "DST", team: "BUF" }];
    expect(suggestPlayers(defenses, picked)).toEqual([]);
    expect(searchPlayers(defenses, picked, "bills")).toEqual([]);
  });
});
