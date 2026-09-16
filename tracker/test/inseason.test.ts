import { beforeEach, describe, expect, it } from "vitest";
import { SELF, env } from "cloudflare:test";
import { ACTUALS_KEY_PREFIX } from "../src/actuals";
import { BOARD_KEY } from "../src/board";
import { INSEASON_KEY_PREFIX, generatedAtMillis, inseasonKey, parseEnvelope, weekFromKey } from "../src/inseason";
import { LEAGUE_BUNDLE_KEY } from "../src/league-bundle";
import boardFixture from "./fixtures/board.json";
import leagueBundle from "./fixtures/league-bundle.json";
import digestFixture from "./fixtures/inseason/digest.json";
import lineupFixture from "./fixtures/inseason/lineup.json";
import retroFixture from "./fixtures/inseason/retro.json";
import rosFixture from "./fixtures/inseason/ros.json";
import type { InseasonView } from "../src/inseason-view";

const KEY = "test-secret-key";
const FIXTURES = { lineup: lineupFixture, digest: digestFixture, retro: retroFixture, ros: rosFixture } as const;
type Kind = keyof typeof FIXTURES;

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function bearer(key = KEY): HeadersInit {
  return { Authorization: `Bearer ${key}` };
}

async function post(kind: string, body: unknown, key = KEY): Promise<Response> {
  return SELF.fetch(`https://x/api/inseason/${kind}`, {
    method: "POST",
    headers: { ...bearer(key), "content-type": "application/json" },
    body: typeof body === "string" ? body : JSON.stringify(body),
  });
}

async function view(query = ""): Promise<{ status: number; body: InseasonView & { error?: string } }> {
  const res = await SELF.fetch(`https://x/api/inseason${query}`, { headers: bearer() });
  return { status: res.status, body: (await res.json()) as InseasonView & { error?: string } };
}

async function clearInseason(): Promise<void> {
  const page = await env.BOARD.list({ prefix: INSEASON_KEY_PREFIX });
  await Promise.all(page.keys.map(({ name }) => env.BOARD.delete(name)));
  const actuals = await env.BOARD.list({ prefix: ACTUALS_KEY_PREFIX });
  await Promise.all(actuals.keys.map(({ name }) => env.BOARD.delete(name)));
  await env.BOARD.delete(LEAGUE_BUNDLE_KEY);
}

describe("parseEnvelope", () => {
  it.each(Object.keys(FIXTURES) as Kind[])("accepts the generated %s fixture", (kind) => {
    const parsed = parseEnvelope(FIXTURES[kind], kind);
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    expect(parsed.envelope.kind).toBe(kind);
    expect(parsed.envelope.week).toBe(1);
  });

  it("rejects a kind that does not match the route", () => {
    const parsed = parseEnvelope(lineupFixture, "digest");
    expect(parsed).toMatchObject({ ok: false, message: expect.stringContaining("route kind digest") });
  });

  it("rejects extra envelope keys, bad versions, and bad timestamps", () => {
    expect(parseEnvelope({ ...lineupFixture, extra: 1 }).ok).toBe(false);
    expect(parseEnvelope({ ...lineupFixture, schema_version: 2 }).ok).toBe(false);
    expect(parseEnvelope({ ...lineupFixture, generated_at: "2026-09-20T14:05:12+02:00" }).ok).toBe(false);
    expect(parseEnvelope({ ...lineupFixture, week: 0 }).ok).toBe(false);
    expect(parseEnvelope({ ...lineupFixture, team_name: 7 }).ok).toBe(false);
  });

  it("accepts every UTC spelling the producer contracts allow", () => {
    // parse_bundle / parse_actuals accept RFC 3339 `+00:00` (and a space
    // separator), and the CLI copies those timestamps into the context.
    const lineup = clone(lineupFixture) as { context: Record<string, unknown>; generated_at: string; report: Record<string, unknown> };
    lineup.context.league_synced_at = "2026-09-12T00:00:00+00:00";
    lineup.context.snapshot_generated_at = "2026-09-12 14:05:12-00:00";
    lineup.report.injury_as_of = "2026-09-12T08:00:00.250+00:00";
    lineup.generated_at = "2026-09-20T14:05:12.000+00:00";
    expect(parseEnvelope(lineup, "lineup")).toMatchObject({ ok: true });
    expect(generatedAtMillis(lineup)).toBe(Date.parse("2026-09-20T14:05:12Z"));

    const retro = clone(retroFixture) as { context: Record<string, unknown> };
    retro.context.actuals_synced_at = "2026-09-16T16:00:00+00:00";
    expect(parseEnvelope(retro, "retro")).toMatchObject({ ok: true });

    for (const bad of ["2026-09-16T16:00:00", "2026-09-16T16:00:00+02:00", "2026-09-16T16:00:00-05:00", "not a time+00:00"]) {
      const rejected = clone(retroFixture) as { context: Record<string, unknown> };
      rejected.context.actuals_synced_at = bad;
      expect(parseEnvelope(rejected, "retro"), bad).toMatchObject({ ok: false, message: expect.stringContaining("actuals_synced_at") });
    }
  });

  it("closes the per-kind context keys", () => {
    const ros = clone(rosFixture) as { context: Record<string, unknown> };
    ros.context.bogus = true;
    expect(parseEnvelope(ros, "ros")).toMatchObject({ ok: false, message: expect.stringContaining("context") });
    const retro = clone(retroFixture) as { context: Record<string, unknown> };
    delete retro.context.actuals_synced_at;
    expect(parseEnvelope(retro, "retro").ok).toBe(false);
  });

  it("checks the report rows the page renders", () => {
    const lineup = clone(lineupFixture) as { report: { start: Array<Record<string, unknown>> } };
    lineup.report.start[0]!.points = "18";
    expect(parseEnvelope(lineup, "lineup")).toMatchObject({ ok: false, message: expect.stringContaining("report.start[0].points") });

    const digest = clone(digestFixture) as { report: { llm: unknown } };
    digest.report.llm = null;
    expect(parseEnvelope(digest, "digest").ok).toBe(false);

    const retro = clone(retroFixture) as { report: { matchup: Record<string, unknown> } };
    retro.report.matchup = { user_points: "41.5", opponent_points: 18 };
    expect(parseEnvelope(retro, "retro").ok).toBe(false);

    const ros = clone(rosFixture) as { report: { usage_available: unknown } };
    ros.report.usage_available = "no";
    expect(parseEnvelope(ros, "ros").ok).toBe(false);
  });

  it("tolerates extra report keys because the report passes through unchanged", () => {
    const ros = clone(rosFixture) as { report: Record<string, unknown> };
    ros.report.future_field = { anything: true };
    expect(parseEnvelope(ros, "ros").ok).toBe(true);
  });

  it("requires retro hindsight keys after the additive publish", () => {
    const retro = clone(retroFixture) as { report: Record<string, unknown> };
    expect(parseEnvelope(retro, "retro").ok).toBe(true);
    delete retro.report.hindsight_total;
    expect(parseEnvelope(retro, "retro")).toMatchObject({
      ok: false,
      message: expect.stringContaining("hindsight_total"),
    });
  });
});

describe("inseason keys", () => {
  it("encodes season, kind, and week and decodes only its own prefix", () => {
    expect(inseasonKey(2026, "retro", 3)).toBe("inseason:v1:2026:retro:3");
    expect(weekFromKey("inseason:v1:2026:retro:3", 2026, "retro")).toBe(3);
    expect(weekFromKey("inseason:v1:2026:retro:03", 2026, "retro")).toBeNull();
    expect(weekFromKey("inseason:v1:2026:lineup:3", 2026, "retro")).toBeNull();
    expect(weekFromKey("inseason:v1:2025:retro:3", 2026, "retro")).toBeNull();
  });
});

describe("Worker POST /api/inseason/{kind}", () => {
  beforeEach(async () => {
    await clearInseason();
    await env.BOARD.put(BOARD_KEY, JSON.stringify(boardFixture));
  });

  it("requires the bearer key and rejects unknown kinds and methods", async () => {
    expect((await SELF.fetch("https://x/api/inseason/lineup", { method: "POST", body: "{}" })).status).toBe(401);
    expect((await post("lineup", lineupFixture, "nope")).status).toBe(401);
    expect((await post("waivers", lineupFixture)).status).toBe(404);
    expect((await post("lineup/extra", lineupFixture)).status).toBe(404);
    const get = await SELF.fetch("https://x/api/inseason/lineup", { headers: bearer() });
    expect(get.status).toBe(405);
    expect(get.headers.get("Allow")).toBe("POST");
    expect(await env.BOARD.get(inseasonKey(2024, "lineup", 1))).toBeNull();
  });

  it.each(Object.keys(FIXTURES) as Kind[])("stores a valid %s envelope verbatim", async (kind) => {
    const res = await post(kind, FIXTURES[kind]);
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({
      kind,
      season: 2024,
      week: 1,
      generated_at: FIXTURES[kind].generated_at,
    });
    expect(await env.BOARD.get(inseasonKey(2024, kind, 1))).toBe(JSON.stringify(FIXTURES[kind]));
  });

  it("returns invalid_report for a shape failure or a kind mismatch", async () => {
    const mismatch = await post("digest", lineupFixture);
    expect(mismatch.status).toBe(400);
    expect(await mismatch.json()).toMatchObject({ error: "invalid_report" });
    const bad = await post("lineup", { ...lineupFixture, report: {} });
    expect(bad.status).toBe(400);
    expect(await bad.json()).toMatchObject({ error: "invalid_report" });
    const notJson = await post("lineup", "{not json");
    expect(notJson.status).toBe(400);
    expect(await notJson.json()).toMatchObject({ error: "invalid_json" });
    expect(await env.BOARD.get(inseasonKey(2024, "lineup", 1))).toBeNull();
  });

  it("orders stored documents by instant, not by timestamp spelling", async () => {
    expect((await post("ros", { ...rosFixture, generated_at: "2026-09-16T11:06:20+00:00" })).status).toBe(200);
    const equal = await post("ros", rosFixture);
    expect(equal.status).toBe(200);
    const older = await post("ros", { ...rosFixture, generated_at: "2026-09-16T11:06:19+00:00" });
    expect(older.status).toBe(409);
  });

  it("rejects an older generated_at with stale_report but accepts an equal one", async () => {
    expect((await post("ros", rosFixture)).status).toBe(200);
    const older = { ...rosFixture, generated_at: "2026-09-16T11:06:19Z" };
    const stale = await post("ros", older);
    expect(stale.status).toBe(409);
    expect(await stale.json()).toMatchObject({ error: "stale_report" });
    expect(await env.BOARD.get(inseasonKey(2024, "ros", 1))).toBe(JSON.stringify(rosFixture));

    const equal = await post("ros", rosFixture);
    expect(equal.status).toBe(200);
    const newer = { ...rosFixture, generated_at: "2026-09-16T11:06:21Z" };
    expect((await post("ros", newer)).status).toBe(200);
    expect(await env.BOARD.get(inseasonKey(2024, "ros", 1))).toBe(JSON.stringify(newer));
  });

  it("rejects a season that differs from the published board", async () => {
    await env.BOARD.put(BOARD_KEY, JSON.stringify({ ...boardFixture, season: 2026 }));
    const res = await post("lineup", lineupFixture);
    expect(res.status).toBe(409);
    expect(await res.json()).toMatchObject({ error: "season_mismatch" });
    await env.BOARD.delete(BOARD_KEY);
    expect((await post("lineup", lineupFixture)).status).toBe(200);
  });

  it("returns 413 for an oversized body before validating", async () => {
    const res = await SELF.fetch("https://x/api/inseason/lineup", {
      method: "POST",
      headers: { ...bearer(), "content-type": "application/json", "content-length": String(11 * 1024 * 1024) },
      body: "{}",
    });
    expect(res.status).toBe(413);
    expect(await res.json()).toMatchObject({ error: "payload_too_large" });
  });
});

describe("Worker GET /api/inseason", () => {
  beforeEach(async () => {
    await clearInseason();
    await env.BOARD.put(BOARD_KEY, JSON.stringify(boardFixture));
  });

  async function seed(kind: Kind, week: number, overrides: Record<string, unknown> = {}): Promise<void> {
    const envelope = { ...clone(FIXTURES[kind]), week, ...overrides };
    await env.BOARD.put(inseasonKey(2024, kind, week), JSON.stringify(envelope));
  }

  it("validates query parameters and falls back to the board season", async () => {
    expect((await view("?season=abc")).status).toBe(400);
    expect((await view("?season=2024&week=0")).status).toBe(400);
    expect((await SELF.fetch("https://x/api/inseason", { method: "POST", headers: bearer() })).status).toBe(405);
    const fallback = await view();
    expect(fallback.status).toBe(200);
    expect(fallback.body.season).toBe(2024);
    expect(fallback.body.week).toBe(1);
    expect(fallback.body.league).toBeNull();
    expect(fallback.body.weeks).toEqual([]);
    expect(fallback.body.actuals_available).toEqual({});
    expect(fallback.body.cards).toEqual({
      lineup: { envelope: null },
      digest: { envelope: null },
      retro: { envelope: null },
      ros: { envelope: null },
    });
    expect(fallback.body.server_now).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/);
  });

  it("requires a season when neither a league bundle nor a board is published", async () => {
    await env.BOARD.delete(BOARD_KEY);
    expect((await view()).status).toBe(400);
    expect((await view("?season=2024")).status).toBe(200);
  });

  it("composes one week: lineup and digest at W, retro at W-1, newest ros at or before W", async () => {
    await seed("lineup", 2);
    await seed("digest", 2);
    await seed("retro", 1);
    await seed("ros", 1);
    await seed("ros", 3);
    await seed("lineup", 3);
    await env.BOARD.put(`${ACTUALS_KEY_PREFIX}2024:1`, "{}");

    const week2 = await view("?season=2024&week=2");
    expect(week2.status).toBe(200);
    expect(week2.body.week).toBe(2);
    expect(week2.body.weeks).toEqual([2, 3]);
    expect(week2.body.actuals_available).toEqual({ "1": true });
    expect(week2.body.cards.lineup.envelope?.week).toBe(2);
    expect(week2.body.cards.digest.envelope?.week).toBe(2);
    expect(week2.body.cards.retro.envelope?.week).toBe(1);
    expect(week2.body.cards.ros.envelope?.week).toBe(1);

    const week3 = await view("?season=2024&week=3");
    expect(week3.body.cards.ros.envelope?.week).toBe(3);
    expect(week3.body.cards.retro.envelope).toBeNull();
    expect(week3.body.cards.digest.envelope).toBeNull();
    expect(week3.body.actuals_available).toEqual({ "2": false });

    const week1 = await view("?season=2024&week=1");
    expect(week1.body.cards.retro.envelope).toBeNull();
    expect(week1.body.cards.ros.envelope?.week).toBe(1);
    expect(week1.body.actuals_available).toEqual({});
  });

  it("defaults the week to the league's current week, then the newest published week", async () => {
    await seed("digest", 4);
    await seed("lineup", 5);
    const newest = await view("?season=2024");
    expect(newest.body.week).toBe(5);
    expect(newest.body.league).toBeNull();

    await env.BOARD.put(LEAGUE_BUNDLE_KEY, JSON.stringify({ ...leagueBundle, league: { ...leagueBundle.league, current_week: 4 } }));
    const current = await view("?season=2024");
    expect(current.body.week).toBe(4);
    expect(current.body.league).toEqual({ synced_at: leagueBundle.synced_at, current_week: 4 });
    expect(current.body.cards.digest.envelope?.week).toBe(4);
    expect(current.body.cards.lineup.envelope).toBeNull();

    const other = await view("?season=2025");
    expect(other.body.league).toBeNull();
    expect(other.body.week).toBe(1);
  });

  it("treats an unreadable stored document as absent", async () => {
    await env.BOARD.put(inseasonKey(2024, "ros", 1), "{not json");
    const res = await view("?season=2024&week=1");
    expect(res.status).toBe(200);
    expect(res.body.cards.ros.envelope).toBeNull();
  });
});
