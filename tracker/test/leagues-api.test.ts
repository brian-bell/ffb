import { beforeEach, describe, expect, it } from "vitest";
import { SELF, env } from "cloudflare:test";
import { LEAGUE_BUNDLE_KEY, leagueBundleKey, leagueKeyFromBundleKey } from "../src/league-bundle";
import { DEFAULT_LEAGUE_KEY } from "../src/league-keys";
import { leagueLabels, sortLeagues, type LeagueOption } from "../src/league-directory";
import fixtureJson from "./fixtures/league-bundle.json";

const KEY = "test-secret-key";
const URL_ = "https://x/api/leagues";
const SLEEPER = "sleeper:1395854363380965376";

function bearer(key = KEY): HeadersInit {
  return { Authorization: `Bearer ${key}` };
}

function bundle(overrides: Record<string, unknown> = {}): string {
  return JSON.stringify({ ...JSON.parse(JSON.stringify(fixtureJson)), ...overrides });
}

/** A bundle relabelled onto another league, for multi-league listings. */
function bundleFor(source: string, leagueKey: string, name: string, week = 3): string {
  const base = JSON.parse(JSON.stringify(fixtureJson)) as Record<string, any>;
  base.source = source;
  base.league = { ...base.league, league_key: leagueKey.split(":").slice(1).join(":"), name, current_week: week };
  return JSON.stringify(base);
}

async function get(key = KEY): Promise<Response> {
  return SELF.fetch(URL_, { headers: bearer(key) });
}

async function clearBundles(): Promise<void> {
  const page = await env.BOARD.list({ prefix: "league:bundle:" });
  for (const { name } of page.keys) await env.BOARD.delete(name);
}

describe("Worker GET /api/leagues", () => {
  beforeEach(clearBundles);

  it("requires the tracker bearer key", async () => {
    expect((await SELF.fetch(URL_)).status).toBe(401);
    expect((await get("nope")).status).toBe(401);
  });

  it("rejects non-GET methods", async () => {
    const res = await SELF.fetch(URL_, { method: "POST", headers: bearer() });
    expect(res.status).toBe(405);
    expect(res.headers.get("Allow")).toBe("GET");
  });

  it("returns an empty directory before any bundle is published", async () => {
    const res = await get();
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ default_league: DEFAULT_LEAGUE_KEY, leagues: [] });
  });

  it("lists one entry per league with the header fields the picker needs", async () => {
    await env.BOARD.put(leagueBundleKey(DEFAULT_LEAGUE_KEY), bundleFor("yahoo", DEFAULT_LEAGUE_KEY, "Money League", 4));
    await env.BOARD.put(leagueBundleKey(SLEEPER), bundleFor("sleeper", SLEEPER, "Dynasty", 3));

    const body = (await (await get()).json()) as { default_league: string; leagues: LeagueOption[] };
    expect(body.default_league).toBe(DEFAULT_LEAGUE_KEY);
    expect(body.leagues.map((l) => l.league_key)).toEqual([DEFAULT_LEAGUE_KEY, SLEEPER]);

    const [yahoo, sleeper] = body.leagues;
    expect(yahoo).toMatchObject({ name: "Money League", source: "yahoo", current_week: 4 });
    expect(sleeper).toMatchObject({ name: "Dynasty", source: "sleeper", current_week: 3 });
    // The user's own team, so the header can name a team with nothing published.
    expect(typeof yahoo!.team_name === "string" || yahoo!.team_name === null).toBe(true);
    expect(yahoo!.season).toBe((JSON.parse(bundle()) as any).league.season);
  });

  it("sorts the default league first and the rest by name", async () => {
    const zed = "sleeper:zzz";
    const abc = "sleeper:aaa";
    await env.BOARD.put(leagueBundleKey(zed), bundleFor("sleeper", zed, "Zed"));
    await env.BOARD.put(leagueBundleKey(abc), bundleFor("sleeper", abc, "Abc"));
    await env.BOARD.put(leagueBundleKey(DEFAULT_LEAGUE_KEY), bundleFor("yahoo", DEFAULT_LEAGUE_KEY, "Money League"));

    const body = (await (await get()).json()) as { leagues: LeagueOption[] };
    expect(body.leagues.map((l) => l.league_key)).toEqual([DEFAULT_LEAGUE_KEY, abc, zed]);
  });

  it("serves the default league from the v1 key when it has no v2 key", async () => {
    await env.BOARD.put(LEAGUE_BUNDLE_KEY, bundleFor("yahoo", DEFAULT_LEAGUE_KEY, "Pre-rekey"));

    const body = (await (await get()).json()) as { leagues: LeagueOption[] };
    expect(body.leagues).toHaveLength(1);
    expect(body.leagues[0]).toMatchObject({ league_key: DEFAULT_LEAGUE_KEY, name: "Pre-rekey" });
  });

  it("prefers the v2 key over v1 and lists the league once", async () => {
    await env.BOARD.put(LEAGUE_BUNDLE_KEY, bundleFor("yahoo", DEFAULT_LEAGUE_KEY, "Old"));
    await env.BOARD.put(leagueBundleKey(DEFAULT_LEAGUE_KEY), bundleFor("yahoo", DEFAULT_LEAGUE_KEY, "New"));

    const body = (await (await get()).json()) as { leagues: LeagueOption[] };
    expect(body.leagues).toHaveLength(1);
    expect(body.leagues[0]!.name).toBe("New");
  });

  it("skips an unreadable bundle rather than failing the whole listing", async () => {
    await env.BOARD.put(leagueBundleKey(SLEEPER), "{not json");
    await env.BOARD.put(leagueBundleKey(DEFAULT_LEAGUE_KEY), bundleFor("yahoo", DEFAULT_LEAGUE_KEY, "Money League"));

    const body = (await (await get()).json()) as { leagues: LeagueOption[] };
    expect(body.leagues.map((l) => l.league_key)).toEqual([DEFAULT_LEAGUE_KEY]);
  });
});

describe("leagueKeyFromBundleKey", () => {
  it("maps the v1 key to the league it held", () => {
    expect(leagueKeyFromBundleKey(LEAGUE_BUNDLE_KEY)).toBe(DEFAULT_LEAGUE_KEY);
  });

  it("round-trips a v2 key, colon and all", () => {
    for (const key of [DEFAULT_LEAGUE_KEY, SLEEPER, "sleeper:a b", "yahoo:x%y"]) {
      expect(leagueKeyFromBundleKey(leagueBundleKey(key))).toBe(key);
    }
  });

  it("rejects keys that are not bundle keys", () => {
    expect(leagueKeyFromBundleKey("inseason:v2:2026:yahoo:lineup:3")).toBeNull();
    expect(leagueKeyFromBundleKey("league:bundle::current")).toBeNull();
    // An unencoded colon in the slug is ambiguous, so it was never one of ours.
    expect(leagueKeyFromBundleKey("league:bundle:yahoo:470.l.1:current")).toBeNull();
    expect(leagueKeyFromBundleKey("league:bundle:yahoo%3Ax")).toBeNull();
    expect(leagueKeyFromBundleKey("league:bundle:%E0%A4%A:current")).toBeNull();
  });
});

describe("league directory ordering and labels", () => {
  const option = (league_key: string, name: string, source: string): LeagueOption => ({
    league_key,
    name,
    source,
    season: 2026,
    current_week: 3,
    num_teams: 10,
    synced_at: "2026-09-17T12:00:00Z",
    team_name: null,
  });

  it("leaves the input array untouched", () => {
    const input = [option("b", "B", "sleeper"), option("a", "A", "yahoo")];
    sortLeagues(input, "zzz");
    expect(input.map((l) => l.league_key)).toEqual(["b", "a"]);
  });

  it("labels distinct names plainly", () => {
    const labels = leagueLabels([option("a", "Money League", "yahoo"), option("b", "Dynasty", "sleeper")]);
    expect(labels.get("a")).toBe("Money League");
    expect(labels.get("b")).toBe("Dynasty");
  });

  it("appends the provider only to names that collide", () => {
    const labels = leagueLabels([
      option("yahoo:1", "Dynasty", "yahoo"),
      option("sleeper:2", "Dynasty", "sleeper"),
      option("sleeper:3", "Redraft", "sleeper"),
    ]);
    expect(labels.get("yahoo:1")).toBe("Dynasty · yahoo");
    expect(labels.get("sleeper:2")).toBe("Dynasty · sleeper");
    expect(labels.get("sleeper:3")).toBe("Redraft");
  });
});
