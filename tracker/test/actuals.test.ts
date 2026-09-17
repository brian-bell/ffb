import { describe, expect, it } from "vitest";
import { SELF } from "cloudflare:test";
import fixtureJson from "./fixtures/weekly-actuals.json";
import { ACTUALS_KEY_PREFIX, parseActuals } from "../src/actuals";

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
    expect(await posted.json()).toEqual({
      ok: true,
      season: 2024,
      week: 1,
      players: 1,
      matchups: 1,
      key: `${ACTUALS_KEY_PREFIX}2024:1`,
    });

    const missing = await SELF.fetch("https://x/api/actuals?season=2024&week=2", {
      headers: bearer(KEY),
    });
    expect(missing.status).toBe(404);

    const loaded = await SELF.fetch("https://x/api/actuals?season=2024&week=1", {
      headers: bearer(KEY),
    });
    expect(loaded.status).toBe(200);
    expect(await loaded.json()).toEqual(fixture);
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
