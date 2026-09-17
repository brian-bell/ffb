import { describe, expect, it } from "vitest";
import { parseBundle, syncedAtMillis, type LeagueBundle } from "../src/league-bundle";
import fixtureJson from "./fixtures/league-bundle.json";

function bundle(change: Record<string, unknown> = {}): Record<string, unknown> {
  return { ...structuredClone(fixtureJson), ...change };
}

function expectReject(payload: unknown, season: number | undefined, message: string): void {
  expect(() => parseBundle(payload, season)).toThrow(message);
}

describe("parseBundle closed LeagueBundle v1", () => {
  it("accepts the committed minimal fixture", () => {
    expect(parseBundle(fixtureJson, 2024)).toEqual(fixtureJson);
    expect(parseBundle(fixtureJson)).toEqual(fixtureJson);
  });

  it("accepts source yahoo and a +00:00 UTC timestamp", () => {
    const data = bundle({
      source: "yahoo",
      synced_at: "2026-09-12T16:00:00+00:00",
    });
    expect(parseBundle(data, 2024).source).toBe("yahoo");
  });

  it("rejects a caller season that does not match the bundle", () => {
    expectReject(fixtureJson, 2026, "requested season 2026 does not match bundle season 2024");
  });

  it.each([
    [{ schema_version: 2 }, "schema_version"],
    [{ extra: "typo" }, "unknown"],
    [{ rosters: [] }, "every team"],
    [{ source: "sleeper" }, "fixture or yahoo"],
  ])("rejects closed-schema drift %j", (change, message) => {
    expectReject(bundle(change), 2024, message);
  });

  it("rejects a roster week that is not league.current_week", () => {
    const data = bundle();
    (data.rosters as Array<{ week: number }>)[0]!.week = 2;
    expectReject(data, 2024, "current_week");
  });

  it("rejects extra or missing player-row keys", () => {
    const extra = bundle();
    (extra.rosters as Array<{ players: unknown[] }>)[0]!.players = [
      {
        yahoo_player_id: "29279",
        yahoo_player_key: "1.p.29279",
        name: "Derrick Henry",
        nfl_team: "BAL",
        primary_position: "RB",
        eligible_positions: ["RB"],
        selected_position: "RB",
        injury: "Q",
      },
    ];
    expectReject(extra, 2024, "unknown or missing fields");

    const missing = bundle();
    (missing.rosters as Array<{ players: unknown[] }>)[0]!.players = [
      {
        yahoo_player_id: "29279",
        yahoo_player_key: "1.p.29279",
        name: "Derrick Henry",
        nfl_team: "BAL",
        primary_position: "RB",
        eligible_positions: ["RB"],
      },
    ];
    expectReject(missing, 2024, "unknown or missing fields");
  });

  it("rejects duplicate Yahoo player IDs and incomplete team coverage", () => {
    const player = {
      yahoo_player_id: "29279",
      yahoo_player_key: "1.p.29279",
      name: "Derrick Henry",
      nfl_team: "BAL",
      primary_position: "RB",
      eligible_positions: ["RB"],
      selected_position: "RB",
    };
    const duplicate = bundle();
    (duplicate.rosters as Array<{ players: unknown[] }>)[0]!.players = [player, { ...player, yahoo_player_key: "1.p.other" }];
    expectReject(duplicate, 2024, "Yahoo player IDs must be unique across league rosters");

    const uncovered = bundle({
      league: { ...fixtureJson.league, num_teams: 2 },
      teams: [
        ...fixtureJson.teams,
        {
          team_id: "2",
          team_key: "1.l.mock-1.t.2",
          name: "Rival",
          managers: ["Alex"],
          is_user_team: false,
        },
      ],
    });
    expectReject(uncovered, 2024, "every team must have exactly one current-week roster");
  });

  it("rejects non-UTC synced_at and non-boolean is_user_team", () => {
    expectReject(bundle({ synced_at: "2026-07-22T12:00:00-04:00" }), 2024, "must be UTC");
    expectReject(bundle({ synced_at: "2026-07-22T12:00:00" }), 2024, "must be UTC");
    expectReject(bundle({ synced_at: "2026-07-22 12:00:00" }), 2024, "must be UTC");
    expectReject(bundle({ synced_at: "not-a-timestamp" }), 2024, "RFC 3339");
    // Calendar-invalid dates must fail like Python parse_bundle, not roll over.
    expectReject(bundle({ synced_at: "2026-02-30T12:00:00Z" }), 2024, "RFC 3339");
    expectReject(bundle({ synced_at: "2026-13-01T12:00:00Z" }), 2024, "RFC 3339");
    expectReject(bundle({ synced_at: "2026-07-22T24:00:00Z" }), 2024, "RFC 3339");
    const team = structuredClone(fixtureJson.teams[0]);
    (team as { is_user_team: unknown }).is_user_team = 1;
    expectReject(bundle({ teams: [team] }), 2024, "is_user_team must be boolean");
  });

  it("rejects duplicate scoring keys and non-finite points", () => {
    const rules = [
      { stat_key: "rec", points: 0.5, provider_stat_id: "11", provider_name: "Receptions" },
      { stat_key: "rec", points: 1, provider_stat_id: "12", provider_name: "Dup" },
    ];
    expectReject(
      bundle({ settings: { ...fixtureJson.settings, scoring_rules: rules } }),
      2024,
      "scoring rule keys and provider stat IDs must be unique",
    );
    expectReject(
      bundle({
        settings: {
          ...fixtureJson.settings,
          scoring_rules: [{ stat_key: "rec", points: Number.POSITIVE_INFINITY, provider_stat_id: "11", provider_name: "Receptions" }],
        },
      }),
      2024,
      "must be a finite number",
    );
  });
});

// Accept/reject corpus for `bundle.synced_at`, identical to SYNCED_AT_ACCEPTED /
// SYNCED_AT_REJECTED in `tests/test_league.py`. The Worker and the CLI must
// agree on exactly which timestamps are valid, in both directions: a form the
// Worker rejects but Python accepts blocks a legitimate producer, and a form the
// Worker accepts but Python rejects stores a bundle the CLI cannot read back.
const SYNCED_AT_ACCEPTED = [
  "2026-07-22T12:00:00Z",
  "2026-07-22T12:00:00+00:00",
  "2026-07-22T12:00:00-00:00",
  "2026-07-22 12:00:00Z",
  "2026-07-22T12:00:00.123456Z",
  "2026-07-22T12:00:00.123456789Z",
  "2026-07-22T12:00:00.1Z",
  "2026-07-22T12:00:00,123Z",
  "2026-07-22T12:00:00+0000",
  "2026-07-22T12:00:00-0000",
  "2026-07-22T12:00:00+00",
  "2026-07-22T12:00:00+00:00:00",
  "2026-07-22T12:00Z",
  "2026-07-22T12:00+00:00",
  "2024-02-29T00:00:00Z",
  "0001-01-01T00:00:00Z",
  "9999-12-31T23:59:59Z",
];

const SYNCED_AT_REJECTED = [
  "2026-07-22T12:00:00",
  "2026-07-22 12:00:00",
  "2026-07-22T12:00:00+01:00",
  "2026-07-22T12:00:00-05:00",
  "2026-02-30T12:00:00Z",
  "2025-02-29T00:00:00Z",
  "2026-13-01T12:00:00Z",
  "2026-07-22T25:00:00Z",
  "2026-07-22T24:00:00Z",
  "2026-07-22T12:00:60Z",
  "0000-01-01T00:00:00Z",
  "2026-W30-3T12:00:00Z",
  "20260722T120000Z",
  "20260722T120000+0000",
  "2026-07-22T12Z",
  "2026-07-22",
  "2026-07-22t12:00:00z",
  "2026-07-22T12:00:00Z ",
  "+2026-07-22T12:00:00Z",
  "2026-7-22T12:00:00Z",
  "not-a-timestamp",
];

describe("synced_at grammar parity with Python parse_bundle", () => {
  it.each(SYNCED_AT_ACCEPTED)("accepts %s", (text) => {
    expect(parseBundle(bundle({ synced_at: text }), 2024)).toBeTruthy();
  });

  it.each(SYNCED_AT_REJECTED)("rejects %s", (text) => {
    expect(() => parseBundle(bundle({ synced_at: text }), 2024)).toThrow(/bundle\.synced_at/);
  });

  it("orders every accepted form on the same timeline", () => {
    const at = (text: string) => syncedAtMillis({ synced_at: text } as LeagueBundle);
    // The same instant spelled several ways must compare equal, so a re-post of
    // an identical bundle is idempotent rather than "stale".
    expect(at("2026-07-22T12:00:00Z")).toBe(at("2026-07-22T12:00:00+00:00"));
    expect(at("2026-07-22T12:00:00Z")).toBe(at("2026-07-22T12:00:00+0000"));
    expect(at("2026-07-22T12:00:00Z")).toBe(at("2026-07-22T12:00:00+00"));
    expect(at("2026-07-22T12:00:00Z")).toBe(at("2026-07-22T12:00Z"));
    expect(at("2026-07-22T12:00:00.500Z")).toBe(at("2026-07-22T12:00:00,500Z"));
    expect(at("2026-07-22T12:00:01Z")).toBeGreaterThan(at("2026-07-22T12:00:00Z"));
    expect(Number.isNaN(at("0001-01-01T00:00:00Z"))).toBe(false);
  });
});
