import { describe, expect, it } from "vitest";
import { indexPlayerIdentities, playersEquivalent, type PlayerIdentity } from "../src/player-identity";

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

  it("indexes every persisted equivalence rule without changing its matches", () => {
    const picked: PlayerIdentity[] = [
      { key: "canonical-one", name: "Same Name", pos: "WR", team: "BUF" },
      { key: "manual:unknown", name: "Mystery Player", pos: null, team: "SF" },
      { key: "manual:no-team", name: "Free Agent", pos: "RB", team: null },
      { key: "sleeper:fallback", name: "A. Brown", pos: "WR", team: "PHI" },
      { key: "sleeper:def", name: "San Francisco 49ers", pos: "DST", team: "SF" },
    ];
    const candidates: PlayerIdentity[] = [
      picked[0],
      { key: "canonical-two", name: "Same Name", pos: "WR", team: "BUF" },
      { key: "manual:canonical-bridge", name: "Same Name", pos: "WR", team: "BUF" },
      { key: "manual:unknown-copy", name: "Mystery Player", pos: "Unknown", team: "SFO" },
      { key: "manual:unknown-other-team", name: "Mystery Player", pos: null, team: "BUF" },
      { key: "manual:no-team-copy", name: "Free Agent", pos: "RB", team: null },
      { key: "sleeper:no-team-copy", name: "Free Agent", pos: "RB", team: null },
      { key: "canonical:a-brown", name: "A Brown", pos: "WR", team: "PHI" },
      { key: "def:SFO", name: "49ers D/ST", pos: "DEF", team: "SFO" },
      { key: "def:BUF", name: "Bills D/ST", pos: "DEF", team: "BUF" },
    ];
    const index = indexPlayerIdentities(picked);

    expect(candidates.map((candidate) => index.match(candidate) !== undefined)).toEqual(
      candidates.map((candidate) => picked.some((snapshot) => playersEquivalent(candidate, snapshot))),
    );
  });
});
