import { describe, expect, it } from "vitest";
import { playersEquivalent } from "../src/player-identity";

describe("player identity", () => {
  it("keeps distinct canonical players separate but collapses equivalent defense and fallback snapshots", () => {
    expect(playersEquivalent(
      { key: "canonical-one", name: "Same Name", pos: "WR", team: "BUF" },
      { key: "canonical-two", name: "Same Name", pos: "WR", team: "BUF" },
    )).toBe(false);
    expect(playersEquivalent(
      { key: "sleeper:buf", name: "Buffalo Bills", pos: "DST", team: "buf" },
      { key: "ffc:BUF", name: "Bills D/ST", pos: "DEF", team: "BUF" },
    )).toBe(true);
    expect(playersEquivalent(
      { key: "manual:one", name: "A. Brown", pos: "WR", team: "PHI" },
      { key: "canonical", name: "A Brown", pos: "WR", team: "PHI" },
    )).toBe(true);
    expect(playersEquivalent(
      { key: "manual:one", name: "Mystery Player", pos: "WR", team: null },
      { key: "manual:two", name: "Mystery Player", pos: "WR", team: null },
    )).toBe(true);
    expect(playersEquivalent(
      { key: "manual:one", name: "Mystery Player", pos: null, team: "BUF" },
      { key: "manual:two", name: "Mystery Player", pos: "Unknown", team: "buf" },
    )).toBe(true);
  });

  it("bridges legacy and canonical defense team aliases", () => {
    expect(playersEquivalent(
      { key: "sleeper:SF", name: "San Francisco 49ers", pos: "DEF", team: "SF" },
      { key: "def:SFO", name: "49ers D/ST", pos: "DEF", team: "SFO" },
    )).toBe(true);
  });

  it("does not treat the canonical defense namespace as a source fallback", () => {
    expect(playersEquivalent(
      { key: "def:SFO", name: "Same Name", pos: "WR", team: "SFO" },
      { key: "canonical-other", name: "Same Name", pos: "WR", team: "SFO" },
    )).toBe(false);
  });
});
