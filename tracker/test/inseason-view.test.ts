import { describe, expect, it } from "vitest";
import type { InseasonEnvelope, InseasonKind, LineupReport, LineupRow } from "../src/inseason";
import { cardFreshness, formatAge, LINEUP_CARD_CLOSE_CALLS, lineupCardRows, oldestSource, type InseasonView, DAY_MS } from "../src/inseason-view";
import digestFixture from "./fixtures/inseason/digest.json";
import lineupFixture from "./fixtures/inseason/lineup.json";
import retroFixture from "./fixtures/inseason/retro.json";
import rosFixture from "./fixtures/inseason/ros.json";

const NOW = Date.parse("2026-09-20T15:40:00Z");

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

type Lineup = Extract<InseasonEnvelope, { kind: "lineup" }>;
type Digest = Extract<InseasonEnvelope, { kind: "digest" }>;
type Retro = Extract<InseasonEnvelope, { kind: "retro" }>;
type Ros = Extract<InseasonEnvelope, { kind: "ros" }>;

interface Cards {
  lineup: Lineup;
  digest: Digest;
  retro: Retro;
  ros: Ros;
}

function fresh(): { view: InseasonView; cards: Cards } {
  const cards: Cards = {
    lineup: clone(lineupFixture) as unknown as Lineup,
    digest: clone(digestFixture) as unknown as Digest,
    retro: clone(retroFixture) as unknown as Retro,
    ros: clone(rosFixture) as unknown as Ros,
  };
  cards.lineup.week = 2;
  cards.lineup.generated_at = "2026-09-20T14:05:12Z";
  cards.lineup.context.league_synced_at = "2026-09-20T12:22:00Z";
  cards.lineup.report.injury_as_of = "2026-09-20T13:02:00Z";
  cards.lineup.report.missing_projections = [];
  cards.lineup.report.undecidable = [];
  cards.digest.week = 2;
  cards.digest.generated_at = "2026-09-20T14:06:40Z";
  cards.digest.report.injury_as_of = "2026-09-20T13:02:00Z";
  cards.digest.report.llm = { haiku: true, sonnet: true, error: null };
  cards.retro.week = 1;
  cards.retro.generated_at = "2026-09-16T11:04:00Z";
  cards.retro.report.missing_actuals = [];
  cards.ros.week = 2;
  cards.ros.generated_at = "2026-09-16T11:06:20Z";
  const view: InseasonView = {
    season: 2026,
    week: 2,
    server_now: "2026-09-20T15:40:00Z",
    league: { synced_at: "2026-09-20T12:22:00Z", current_week: 2 },
    actuals_available: { "1": true },
    weeks: [1, 2],
    cards: {
      lineup: { envelope: cards.lineup },
      digest: { envelope: cards.digest },
      retro: { envelope: cards.retro },
      ros: { envelope: cards.ros },
    },
  };
  return { view, cards };
}

describe("cardFreshness", () => {
  it.each(["lineup", "digest", "retro", "ros"] as InseasonKind[])("%s is fresh on the Sunday baseline", (kind) => {
    const { view } = fresh();
    expect(cardFreshness(kind, view, NOW)).toEqual({ state: "fresh", reason: "" });
  });

  const table: Array<{
    name: string;
    kind: InseasonKind;
    mutate: (view: InseasonView, cards: Cards) => number | void;
    expected: { state: string; reason: string };
  }> = [
    {
      name: "lineup missing",
      kind: "lineup",
      mutate: (view) => { view.cards.lineup.envelope = null; },
      expected: { state: "missing", reason: "Not published for week 2" },
    },
    {
      name: "lineup roster changed after build",
      kind: "lineup",
      mutate: (view) => { view.league = { synced_at: "2026-09-20T15:10:00Z", current_week: 2 }; },
      expected: { state: "stale", reason: "Roster changed after this lineup was built" },
    },
    {
      name: "lineup ignores a league bundle when none is stored",
      kind: "lineup",
      mutate: (view, cards) => { view.league = null; cards.lineup.context.league_synced_at = "2026-01-01T00:00:00Z"; },
      expected: { state: "fresh", reason: "" },
    },
    {
      name: "lineup newer injury report in the digest",
      kind: "lineup",
      mutate: (_view, cards) => { cards.digest.report.injury_as_of = "2026-09-20T14:30:00Z"; },
      expected: { state: "stale", reason: "Newer injury report available" },
    },
    {
      name: "lineup null injury_as_of never counts as stale",
      kind: "lineup",
      mutate: (_view, cards) => { cards.lineup.report.injury_as_of = null; cards.digest.report.injury_as_of = "2026-09-20T14:30:00Z"; },
      expected: { state: "fresh", reason: "" },
    },
    {
      name: "lineup roster rule wins over injury rule",
      kind: "lineup",
      mutate: (view, cards) => { view.league = { synced_at: "2026-09-20T15:10:00Z", current_week: 2 }; cards.digest.report.injury_as_of = "2026-09-20T14:30:00Z"; },
      expected: { state: "stale", reason: "Roster changed after this lineup was built" },
    },
    {
      name: "lineup older than 5 days",
      kind: "lineup",
      mutate: () => NOW + 6 * DAY_MS,
      expected: { state: "stale", reason: "Built more than 5 days ago" },
    },
    {
      name: "lineup exactly 5 days old is not stale",
      kind: "lineup",
      mutate: () => Date.parse("2026-09-20T14:05:12Z") + 5 * DAY_MS,
      expected: { state: "fresh", reason: "" },
    },
    {
      name: "lineup missing projections",
      kind: "lineup",
      mutate: (_view, cards) => {
        cards.lineup.report.missing_projections = [{ name: "A", position: "RB", team: null, slot: "BN", points: null, selected_position: "BN" }];
        cards.lineup.report.undecidable = [{ name: "B", position: "WR", team: null, slot: "WR", points: null, selected_position: "WR" }];
      },
      expected: { state: "degraded", reason: "2 players have no projection" },
    },
    {
      name: "lineup one undecidable player",
      kind: "lineup",
      mutate: (_view, cards) => {
        cards.lineup.report.undecidable = [{ name: "B", position: "WR", team: null, slot: "WR", points: null, selected_position: "WR" }];
      },
      expected: { state: "degraded", reason: "1 player has no projection" },
    },
    {
      name: "lineup stale age wins over degraded",
      kind: "lineup",
      mutate: (_view, cards) => {
        cards.lineup.report.undecidable = [{ name: "B", position: "WR", team: null, slot: "WR", points: null, selected_position: "WR" }];
        return NOW + 6 * DAY_MS;
      },
      expected: { state: "stale", reason: "Built more than 5 days ago" },
    },
    {
      name: "news missing",
      kind: "digest",
      mutate: (view) => { view.cards.digest.envelope = null; },
      expected: { state: "missing", reason: "Not published for week 2" },
    },
    {
      name: "news older than 5 days",
      kind: "digest",
      mutate: () => NOW + 6 * DAY_MS,
      expected: { state: "stale", reason: "Headlines are more than 5 days old" },
    },
    {
      name: "news LLM skipped",
      kind: "digest",
      mutate: (_view, cards) => { cards.digest.report.llm = { haiku: false, sonnet: false, error: "LLM skipped (no key)." }; },
      expected: { state: "degraded", reason: "LLM skipped — headlines only" },
    },
    {
      name: "news stale wins over LLM skipped",
      kind: "digest",
      mutate: (_view, cards) => { cards.digest.report.llm.error = "LLM skipped"; return NOW + 6 * DAY_MS; },
      expected: { state: "stale", reason: "Headlines are more than 5 days old" },
    },
    {
      name: "retro week 1",
      kind: "retro",
      mutate: (view) => { view.week = 1; view.cards.retro.envelope = null; view.actuals_available = {}; },
      expected: { state: "waiting", reason: "No prior week to grade" },
    },
    {
      name: "retro waiting for actuals",
      kind: "retro",
      mutate: (view) => { view.cards.retro.envelope = null; view.actuals_available = { "1": false }; },
      expected: { state: "waiting", reason: "Waiting for week 1 actuals" },
    },
    {
      name: "retro actuals present but not published",
      kind: "retro",
      mutate: (view) => { view.cards.retro.envelope = null; },
      expected: { state: "missing", reason: "Actuals are in; retro not published" },
    },
    {
      name: "retro missing actuals",
      kind: "retro",
      mutate: (_view, cards) => { cards.retro.report.missing_actuals = [{ name: "Jake Bates" }]; },
      expected: { state: "degraded", reason: "1 player has no actuals" },
    },
    {
      name: "retro never ages out",
      kind: "retro",
      mutate: () => NOW + 40 * DAY_MS,
      expected: { state: "fresh", reason: "" },
    },
    {
      name: "ros missing",
      kind: "ros",
      mutate: (view) => { view.cards.ros.envelope = null; },
      expected: { state: "missing", reason: "Not published" },
    },
    {
      name: "ros older than 8 days",
      kind: "ros",
      mutate: () => Date.parse("2026-09-16T11:06:20Z") + 8 * DAY_MS + 1,
      expected: { state: "stale", reason: "Built more than 8 days ago" },
    },
    {
      name: "ros at 8 days stays fresh",
      kind: "ros",
      mutate: () => Date.parse("2026-09-16T11:06:20Z") + 8 * DAY_MS,
      expected: { state: "fresh", reason: "" },
    },
  ];

  it.each(table)("$name", ({ kind, mutate, expected }) => {
    const { view, cards } = fresh();
    const now = mutate(view, cards) ?? NOW;
    expect(cardFreshness(kind, view, now)).toEqual(expected);
  });
});

describe("oldestSource and formatAge", () => {
  it("names the oldest published card and skips absent ones", () => {
    const { view } = fresh();
    expect(oldestSource(view, NOW)).toEqual({ kind: "retro", ageMs: NOW - Date.parse("2026-09-16T11:04:00Z") });
    view.cards.retro.envelope = null;
    expect(oldestSource(view, NOW)?.kind).toBe("ros");
    view.cards.ros.envelope = null;
    view.cards.lineup.envelope = null;
    view.cards.digest.envelope = null;
    expect(oldestSource(view, NOW)).toBeNull();
  });

  it("formats minutes, hours, then days", () => {
    expect(formatAge(0)).toBe("0m");
    expect(formatAge(59 * 60_000)).toBe("59m");
    expect(formatAge(5 * 3_600_000)).toBe("5h");
    expect(formatAge(35 * 3_600_000)).toBe("35h");
    expect(formatAge(4 * DAY_MS + 3_600_000)).toBe("4d");
  });
});

describe("lineupCardRows", () => {
  const player = (name: string, slot: string, points: number): LineupRow => ({
    name, position: slot, team: null, slot, points, selected_position: null,
  });
  const report = (overrides: Partial<LineupReport>): LineupReport => ({
    start: [], sit: [], undecidable: [], missing_projections: [], aligned: [], close_calls: [],
    current_total: 0, optimal_total: 0, delta: 0, injury_as_of: null, ...overrides,
  });

  it("folds a swap that is also a close call into its close row", () => {
    const rows = lineupCardRows(report({
      start: [player("Jared Goff", "QB", 17.0), player("Derrick Henry", "RB", 20.0)],
      sit: [player("Trevor Lawrence", "QB", 16.9), player("Zack Moss", "RB", 8.0)],
      close_calls: [
        { name: "Trevor Lawrence", points: 16.9, versus: "Jared Goff", slot: "QB", delta: 0.1 },
        { name: "Wan'Dale Robinson", points: 11.0, versus: "Brian Thomas Jr.", slot: "W/R/T", delta: 0.4 },
        { name: "Tyjae Spears", points: 9.0, versus: "Derrick Henry", slot: "RB", delta: 1.2 },
      ],
    }));
    expect(rows.start.map((row) => row.name)).toEqual(["Derrick Henry"]);
    expect(rows.sit.map((row) => row.name)).toEqual(["Zack Moss"]);
    expect(rows.close.map((call) => [call.name, call.swap])).toEqual([
      ["Trevor Lawrence", true],
      ["Wan'Dale Robinson", false],
      ["Tyjae Spears", false],
    ]);
  });

  it("always shows a folded close call even past the four-row cap", () => {
    const rows = lineupCardRows(report({
      start: [player("Jared Goff", "QB", 17.0)],
      sit: [player("Trevor Lawrence", "QB", 16.0)],
      close_calls: [
        { name: "A", points: 16.8, versus: "B", slot: "W/R/T", delta: 0.2 },
        { name: "C", points: 16.7, versus: "D", slot: "W/R/T", delta: 0.3 },
        { name: "E", points: 16.6, versus: "F", slot: "W/R/T", delta: 0.4 },
        { name: "G", points: 16.5, versus: "H", slot: "W/R/T", delta: 0.5 },
        { name: "Trevor Lawrence", points: 16.0, versus: "Jared Goff", slot: "QB", delta: 1.0 },
      ],
    }));
    expect(LINEUP_CARD_CLOSE_CALLS).toBe(4);
    expect(rows.start).toEqual([]);
    expect(rows.sit).toEqual([]);
    expect(rows.close.map((call) => call.name)).toEqual(["Trevor Lawrence", "A", "C", "E"]);
  });

  it("pairs folded swaps one-to-one when two sitters are close to the same starter", () => {
    const rows = lineupCardRows(report({
      start: [player("C", "RB", 10.0), player("D", "RB", 9.5)],
      sit: [player("A", "RB", 9.4), player("B", "RB", 9.3)],
      close_calls: [
        { name: "A", points: 9.4, versus: "C", slot: "RB", delta: 0.6 },
        { name: "B", points: 9.3, versus: "C", slot: "RB", delta: 0.7 },
      ],
    }));
    expect(rows.start).toEqual([]);
    expect(rows.sit).toEqual([]);
    expect(rows.close.map((call) => [call.versus, call.name, call.delta, call.swap])).toEqual([
      ["D", "B", 0.2, true],
      ["C", "A", 0.6, true],
    ]);
  });

  it("does not fold a swap whose own gap is wider than the sitter's close call", () => {
    const rows = lineupCardRows(report({
      start: [player("C", "RB", 12.0), player("D", "RB", 9.5)],
      sit: [player("A", "RB", 9.4), player("B", "RB", 9.3)],
      close_calls: [{ name: "B", points: 9.3, versus: "D", slot: "RB", delta: 0.2 }],
    }));
    // Best in pairs with best out: C↔A (2.6) and D↔B (0.2). Only D↔B folds.
    expect(rows.start.map((row) => row.name)).toEqual(["C"]);
    expect(rows.sit.map((row) => row.name)).toEqual(["A"]);
    expect(rows.close.map((call) => [call.versus, call.name, call.delta, call.swap])).toEqual([["D", "B", 0.2, true]]);
  });

  it("rounds swap gaps like the Python report, ties to even", () => {
    // Python: round(10.25, 1) == 10.2, round(-1.25, 1) == -1.2.
    const rows = lineupCardRows(report({
      start: [player("C", "RB", 10.25), player("K1", "K", 0.0)],
      sit: [player("A", "RB", 9.0), player("K2", "K", -1.25)],
      close_calls: [
        { name: "A", points: 9.0, versus: "C", slot: "RB", delta: 1.2 },
        { name: "K2", points: -1.25, versus: "K1", slot: "K", delta: 1.2 },
      ],
    }));
    expect(rows.start).toEqual([]);
    expect(rows.sit).toEqual([]);
    expect(rows.close.map((call) => [call.versus, call.delta, call.swap])).toEqual([["C", 1.2, true], ["K1", 1.2, true]]);
  });

  it("pins folded close calls ahead of plain ones with smaller gaps", () => {
    const rows = lineupCardRows(report({
      start: [player("Ravens", "DEF", 8.0)],
      sit: [player("Rams", "DEF", 7.5)],
      close_calls: [
        { name: "Parker Washington", points: 9.0, versus: "Rico Dowdle", slot: "W/R/T", delta: 0.2 },
        { name: "Rams", points: 7.5, versus: "Ravens", slot: "DEF", delta: 0.5 },
      ],
    }));
    expect(rows.close.map((call) => [call.name, call.swap])).toEqual([["Rams", true], ["Parker Washington", false]]);
  });

  it("keeps start and sit when the close call pairs different players", () => {
    const rows = lineupCardRows(report({
      start: [player("Jared Goff", "QB", 17.0)],
      sit: [player("Trevor Lawrence", "QB", 14.0)],
      close_calls: [{ name: "Trevor Lawrence", points: 14.0, versus: "Someone Else", slot: "SUPERFLEX", delta: 1.0 }],
    }));
    expect(rows.start.map((row) => row.name)).toEqual(["Jared Goff"]);
    expect(rows.sit.map((row) => row.name)).toEqual(["Trevor Lawrence"]);
    expect(rows.close.map((call) => [call.name, call.swap])).toEqual([["Trevor Lawrence", false]]);
  });
});
