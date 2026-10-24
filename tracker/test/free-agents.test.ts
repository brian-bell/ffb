import { describe, expect, it } from "vitest";
import { SELF, env } from "cloudflare:test";
import fixtureJson from "./fixtures/free-agents.json";
import {
  freeAgentsKey,
  freeAgentsLeagueKey,
  parseFreeAgents,
  type FreeAgentsBundle,
} from "../src/free-agents";
import { DEFAULT_LEAGUE_KEY } from "../src/league-keys";

const KEY = "test-secret-key";
const fixture = fixtureJson as Record<string, unknown>;

function bearer(key: string): HeadersInit {
  return { Authorization: `Bearer ${key}` };
}

function post(body: unknown, query = ""): Promise<Response> {
  return SELF.fetch(`https://x/api/free-agents${query}`, {
    method: "POST",
    headers: { ...bearer(KEY), "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

function clone(): FreeAgentsBundle {
  return structuredClone(fixture) as unknown as FreeAgentsBundle;
}

describe("parseFreeAgents", () => {
  it("accepts the closed v1 fixture and a null NFL team", () => {
    const bundle = clone();
    bundle.players[0].nfl_team = null;
    const parsed = parseFreeAgents(bundle, { season: 2024 });
    expect(parsed.ok).toBe(true);
    if (parsed.ok) expect(parsed.bundle.players[0].nfl_team).toBeNull();
  });

  it.each([
    ["an unknown key", (b: Record<string, unknown>) => (b.extra = 1), "unknown or missing"],
    ["schema v2", (b: Record<string, unknown>) => (b.schema_version = 2), "schema_version"],
    ["a sleeper source", (b: Record<string, unknown>) => (b.source = "sleeper"), "fixture or yahoo"],
    ["an unparseable timestamp", (b: Record<string, unknown>) => (b.synced_at = "nope"), "RFC 3339"],
    [
      "a space-separated timestamp",
      (b: Record<string, unknown>) => (b.synced_at = "2024-09-12 14:00:00Z"),
      "RFC 3339",
    ],
    [
      "an impossible calendar date",
      (b: Record<string, unknown>) => (b.synced_at = "2024-02-30T14:00:00Z"),
      "RFC 3339",
    ],
    [
      "an out-of-range time",
      (b: Record<string, unknown>) => (b.synced_at = "2024-09-12T24:00:00Z"),
      "RFC 3339",
    ],
    ["an empty pool", (b: Record<string, unknown>) => (b.players = []), "failed scrape"],
  ])("rejects %s", (_name, change, message) => {
    const bundle = structuredClone(fixture);
    change(bundle);
    const parsed = parseFreeAgents(bundle, { season: 2024 });
    expect(parsed.ok).toBe(false);
    if (!parsed.ok) expect(parsed.message).toContain(message);
  });

  it("rejects duplicate ids, bad eligibility, and a season mismatch", () => {
    const dup = clone();
    dup.players.push({ ...dup.players[0] });
    expect(parseFreeAgents(dup).ok).toBe(false);
    const eligibility = clone();
    (eligibility.players[0] as unknown as Record<string, unknown>).eligible_positions = "RB";
    expect(parseFreeAgents(eligibility).ok).toBe(false);
    expect(parseFreeAgents(fixture, { season: 2026 }).ok).toBe(false);
  });
});

describe("Worker /api/free-agents", () => {
  it("requires the bearer key", async () => {
    const none = await SELF.fetch("https://x/api/free-agents", {
      method: "POST",
      body: JSON.stringify(fixture),
    });
    expect(none.status).toBe(401);
    const wrong = await SELF.fetch("https://x/api/free-agents?season=2024", {
      headers: bearer("nope"),
    });
    expect(wrong.status).toBe(401);
  });

  it("rejects a closed-schema violation (400) and unreadable JSON", async () => {
    const res = await post({ ...fixture, schema_version: 3 });
    expect(res.status).toBe(400);
    expect(await res.json()).toMatchObject({ error: "invalid_free_agents" });
    const garbled = await SELF.fetch("https://x/api/free-agents", {
      method: "POST",
      headers: bearer(KEY),
      body: "{",
    });
    expect(garbled.status).toBe(400);
  });

  it("stores a non-default Yahoo pool only under a named league, then serves it there", async () => {
    const league = freeAgentsLeagueKey(clone());
    expect(league).toBe("yahoo:423.l.1");
    const posted = await post(fixture);
    expect(posted.status).toBe(200);
    expect(await posted.json()).toEqual({
      ok: true,
      season: 2024,
      week: 2,
      players: 1,
      synced_at: "2024-09-12T14:00:00Z",
      key: freeAgentsKey(2024, league),
    });

    const unnamed = await SELF.fetch("https://x/api/free-agents?season=2024", {
      headers: bearer(KEY),
    });
    expect(unnamed.status).toBe(404);
    const loaded = await SELF.fetch(
      `https://x/api/free-agents?season=2024&league=${encodeURIComponent(league)}`,
      { headers: bearer(KEY) },
    );
    expect(loaded.status).toBe(200);
    expect(await loaded.json()).toEqual(fixture);
  });

  it("serves the default Yahoo league without ?league=", async () => {
    const bundle = clone();
    bundle.source = "yahoo";
    bundle.league = { ...bundle.league, league_id: "928421", league_key: "470.l.928421", season: 2026 };
    const posted = await post(bundle);
    expect(posted.status).toBe(200);
    expect(await posted.json()).toMatchObject({ key: freeAgentsKey(2026, DEFAULT_LEAGUE_KEY) });
    const loaded = await SELF.fetch("https://x/api/free-agents?season=2026", {
      headers: bearer(KEY),
    });
    expect(loaded.status).toBe(200);
    expect(await loaded.json()).toMatchObject({ source: "yahoo", league: { season: 2026 } });
  });

  it("makes a non-default Yahoo bundle name its slot", async () => {
    const bundle = clone();
    bundle.source = "yahoo";
    const unnamed = await post(bundle);
    expect(unnamed.status).toBe(400);
    expect(await unnamed.json()).toMatchObject({ error: "invalid_free_agents" });
    const named = await post(bundle, "?league=yahoo%3A423.l.1");
    expect(named.status).toBe(200);
  });

  it("rejects a blank league and a league that disagrees with the bundle", async () => {
    const blank = await SELF.fetch("https://x/api/free-agents?season=2024&league=%20", {
      headers: bearer(KEY),
    });
    expect(blank.status).toBe(400);
    expect(await blank.json()).toMatchObject({ error: "invalid_request" });
    const mismatch = await post(fixture, "?league=sleeper%3A1395854363380965376");
    expect(mismatch.status).toBe(400);
    expect(await mismatch.json()).toMatchObject({ error: "invalid_free_agents" });
  });

  it("refuses an older pool over a newer one but accepts a replay", async () => {
    const fresh = clone();
    fresh.league = { ...fresh.league, league_key: "423.l.stale", league_id: "9" };
    fresh.synced_at = "2024-09-12T18:00:00Z";
    const query = "?league=yahoo%3A423.l.stale";
    expect((await post(fresh, query)).status).toBe(200);
    expect((await post(fresh, query)).status).toBe(200);
    const older = { ...fresh, synced_at: "2024-09-12T09:00:00Z" };
    const stale = await post(older, query);
    expect(stale.status).toBe(409);
    expect(await stale.json()).toMatchObject({ error: "stale_free_agents" });
    const stored = JSON.parse(
      (await env.BOARD.get(freeAgentsKey(2024, "yahoo:423.l.stale"))) as string,
    );
    expect(stored.synced_at).toBe("2024-09-12T18:00:00Z");
  });

  it("requires a season on GET and a known method", async () => {
    const noSeason = await SELF.fetch("https://x/api/free-agents", { headers: bearer(KEY) });
    expect(noSeason.status).toBe(400);
    const del = await SELF.fetch("https://x/api/free-agents", {
      method: "DELETE",
      headers: bearer(KEY),
    });
    expect(del.status).toBe(405);
    expect(del.headers.get("Allow")).toBe("GET, POST");
  });
});
