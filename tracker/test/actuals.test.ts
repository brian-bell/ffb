import { describe, expect, it } from "vitest";
import { SELF, env } from "cloudflare:test";
import fixtureJson from "./fixtures/weekly-actuals.json";
import {
  actualsKey,
  actualsKeyV1,
  actualsLeagueKey,
  actualsReadKeys,
  parseActuals,
  type WeeklyActualsBundle,
} from "../src/actuals";
import { DEFAULT_LEAGUE_KEY } from "../src/league-keys";

const KEY = "test-secret-key";
const fixture = fixtureJson as Record<string, unknown>;

function bearer(key: string): HeadersInit {
  return { Authorization: `Bearer ${key}` };
}

describe("parseActuals", () => {
  it("accepts the closed v2 fixture", () => {
    const parsed = parseActuals(fixture, { season: 2024 });
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    expect(parsed.bundle.league.week).toBe(1);
    expect(parsed.bundle.players).toHaveLength(1);
  });

  it("rejects an unknown key and a season mismatch", () => {
    expect(parseActuals({ ...fixture, extra: true }, { season: 2024 }).ok).toBe(false);
    expect(parseActuals(fixture, { season: 2026 }).ok).toBe(false);
  });
});

describe("Worker /api/actuals", () => {
  it("rejects a missing key (401)", async () => {
    const res = await SELF.fetch("https://x/api/actuals", {
      method: "POST",
      body: JSON.stringify(fixture),
    });
    expect(res.status).toBe(401);
  });

  it("rejects a wrong key (401)", async () => {
    const res = await SELF.fetch("https://x/api/actuals", {
      method: "POST",
      headers: bearer("nope"),
      body: JSON.stringify(fixture),
    });
    expect(res.status).toBe(401);
  });

  it("rejects a closed-schema violation (400)", async () => {
    const res = await SELF.fetch("https://x/api/actuals", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ ...fixture, schema_version: 3 }),
    });
    expect(res.status).toBe(400);
    expect(await res.json()).toMatchObject({ error: "invalid_actuals" });
  });

  it("stores a valid bundle in KV and returns it on GET", async () => {
    const posted = await SELF.fetch("https://x/api/actuals", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify(fixture),
    });
    expect(posted.status).toBe(200);
    const league = actualsLeagueKey(fixture as unknown as WeeklyActualsBundle);
    expect(league).toBe("yahoo:1.l.sit");
    expect(await posted.json()).toEqual({
      ok: true,
      season: 2024,
      week: 1,
      players: 1,
      matchups: 1,
      key: actualsKey(2024, 1, league),
    });
    expect(await env.BOARD.get(actualsKeyV1(2024, 1))).toBeNull();

    const missing = await SELF.fetch("https://x/api/actuals?season=2024&week=2", {
      headers: bearer(KEY),
    });
    expect(missing.status).toBe(404);

    const unnamed = await SELF.fetch("https://x/api/actuals?season=2024&week=1", {
      headers: bearer(KEY),
    });
    expect(unnamed.status).toBe(404);

    const loaded = await SELF.fetch(
      `https://x/api/actuals?season=2024&week=1&league=${encodeURIComponent(league)}`,
      { headers: bearer(KEY) },
    );
    expect(loaded.status).toBe(200);
    expect(await loaded.json()).toEqual(fixture);
  });

  it("serves an unpartitioned v1 blob as the default league and prefers a v2 write", async () => {
    await env.BOARD.put(actualsKeyV1(2026, 2), JSON.stringify({ legacy: true }));
    const legacy = await SELF.fetch("https://x/api/actuals?season=2026&week=2", {
      headers: bearer(KEY),
    });
    expect(legacy.status).toBe(200);
    expect(await legacy.json()).toEqual({ legacy: true });

    const sleeperOnly = await SELF.fetch(
      "https://x/api/actuals?season=2026&week=2&league=sleeper%3A1395854363380965376",
      { headers: bearer(KEY) },
    );
    expect(sleeperOnly.status).toBe(404);

    const bundle = structuredClone(fixture) as unknown as WeeklyActualsBundle;
    bundle.source = "yahoo";
    bundle.league = { ...bundle.league, league_id: "928421", league_key: "470.l.928421", season: 2026, week: 2 };
    bundle.matchups = bundle.matchups.map((matchup) => ({ ...matchup, week: 2 }));
    const posted = await SELF.fetch("https://x/api/actuals", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify(bundle),
    });
    expect(posted.status).toBe(200);
    expect(await posted.json()).toMatchObject({ key: actualsKey(2026, 2, DEFAULT_LEAGUE_KEY) });
    const current = await SELF.fetch("https://x/api/actuals?season=2026&week=2", {
      headers: bearer(KEY),
    });
    expect(current.status).toBe(200);
    expect(await current.json()).toMatchObject({ source: "yahoo", league: { week: 2 } });
  });

  it("keeps a Sleeper bundle out of the Yahoo slot", async () => {
    const bundle = structuredClone(fixture) as unknown as WeeklyActualsBundle;
    bundle.source = "sleeper";
    bundle.league = {
      ...bundle.league,
      league_id: "1395854363380965376",
      league_key: "sleeper:1395854363380965376",
    };
    const sleeper = "sleeper:1395854363380965376";
    const posted = await SELF.fetch("https://x/api/actuals", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify(bundle),
    });
    expect(posted.status).toBe(200);
    expect(await posted.json()).toMatchObject({ key: actualsKey(2024, 1, sleeper) });

    const loaded = await SELF.fetch(
      `https://x/api/actuals?season=2024&week=1&league=${encodeURIComponent(sleeper)}`,
      { headers: bearer(KEY) },
    );
    expect(loaded.status).toBe(200);
    expect(await loaded.json()).toMatchObject({ source: "sleeper" });

    const yahoo = await SELF.fetch("https://x/api/actuals?season=2024&week=1", {
      headers: bearer(KEY),
    });
    expect(yahoo.status).toBe(404);
  });

  it("rejects a blank league and a league that disagrees with the bundle", async () => {
    const blank = await SELF.fetch("https://x/api/actuals?season=2024&week=1&league=%20", {
      headers: bearer(KEY),
    });
    expect(blank.status).toBe(400);
    expect(await blank.json()).toMatchObject({ error: "invalid_request" });

    const mismatch = await SELF.fetch(
      "https://x/api/actuals?league=sleeper%3A1395854363380965376",
      {
        method: "POST",
        headers: { ...bearer(KEY), "content-type": "application/json" },
        body: JSON.stringify(fixture),
      },
    );
    expect(mismatch.status).toBe(400);
    expect(await mismatch.json()).toMatchObject({ error: "invalid_actuals" });
  });

  it("makes a non-default Yahoo bundle name its slot, then serves it there", async () => {
    const bundle = structuredClone(fixture) as unknown as WeeklyActualsBundle;
    bundle.source = "yahoo";
    bundle.league = { ...bundle.league, league_id: "928421", league_key: "461.l.928421" };
    const stale = "yahoo:461.l.928421";
    const unnamed = await SELF.fetch("https://x/api/actuals", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify(bundle),
    });
    expect(unnamed.status).toBe(400);
    expect(await unnamed.json()).toMatchObject({ error: "invalid_actuals" });
    expect(await env.BOARD.get(actualsKey(2024, 1, stale))).toBeNull();

    const named = await SELF.fetch(`https://x/api/actuals?league=${encodeURIComponent(stale)}`, {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify(bundle),
    });
    expect(named.status).toBe(200);
    expect(await named.json()).toMatchObject({ key: actualsKey(2024, 1, stale) });
  });

  it("serves a v1 blob to any Yahoo league, never to Sleeper", async () => {
    await env.BOARD.put(actualsKeyV1(2025, 3), JSON.stringify({ legacy: 2025 }));
    const prior = await SELF.fetch(
      `https://x/api/actuals?season=2025&week=3&league=${encodeURIComponent("yahoo:461.l.928421")}`,
      { headers: bearer(KEY) },
    );
    expect(prior.status).toBe(200);
    expect(await prior.json()).toEqual({ legacy: 2025 });

    const sleeper = await SELF.fetch(
      "https://x/api/actuals?season=2025&week=3&league=sleeper%3A1395854363380965376",
      { headers: bearer(KEY) },
    );
    expect(sleeper.status).toBe(404);
  });

  it("rejects GET without season and week (400)", async () => {
    const res = await SELF.fetch("https://x/api/actuals", { headers: bearer(KEY) });
    expect(res.status).toBe(400);
  });

  it("rejects an unknown method (405)", async () => {
    const res = await SELF.fetch("https://x/api/actuals", {
      method: "PUT",
      headers: bearer(KEY),
    });
    expect(res.status).toBe(405);
  });
});

describe("actualsReadKeys", () => {
  it("falls back to v1 only for Yahoo leagues", () => {
    expect(actualsReadKeys(2026, 2)).toEqual([
      actualsKey(2026, 2, DEFAULT_LEAGUE_KEY),
      actualsKeyV1(2026, 2),
    ]);
    expect(actualsReadKeys(2025, 2, "yahoo:461.l.928421")).toEqual([
      actualsKey(2025, 2, "yahoo:461.l.928421"),
      actualsKeyV1(2025, 2),
    ]);
    expect(actualsReadKeys(2026, 2, "sleeper:1395854363380965376")).toEqual([
      actualsKey(2026, 2, "sleeper:1395854363380965376"),
    ]);
  });
});

describe("parseActuals schema-v1 back-compat shim", () => {
  function v1Actuals(extra: Record<string, unknown> = {}): Record<string, unknown> {
    const data = structuredClone(fixture);
    data.schema_version = 1;
    data.players = (data.players as Array<Record<string, unknown>>).map((player) => {
      const { native_id, native_player_key, ...rest } = player;
      return { ...rest, yahoo_player_id: native_id, yahoo_player_key: native_player_key, ...extra };
    });
    return data;
  }

  it("upgrades a v1 payload's identity fields to the v2 spelling", () => {
    const parsed = parseActuals(v1Actuals(), { season: 2024 });
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    expect(parsed.bundle.schema_version).toBe(2);
    expect(parsed.bundle.players[0]!.native_id).toBeTruthy();
    expect(parsed.bundle.players[0]).not.toHaveProperty("yahoo_player_id");
  });

  it("rejects a payload that mixes v1 and v2 identity fields", () => {
    const parsed = parseActuals(v1Actuals({ native_id: "collide" }), { season: 2024 });
    expect(parsed.ok).toBe(false);
    if (parsed.ok) return;
    expect(parsed.message).toContain("mix schema-v1 and schema-v2");
  });
});
