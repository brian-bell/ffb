import { beforeEach, describe, expect, it } from "vitest";
import { SELF, env } from "cloudflare:test";
import { BOARD_KEY } from "../src/board";
import { LEAGUE_BUNDLE_KEY } from "../src/league-bundle";
import fixtureJson from "./fixtures/league-bundle.json";
import boardFixture from "./fixtures/board.json";

const KEY = "test-secret-key";
const BUNDLE_URL = "https://x/api/league/bundle";
const fixtureText = JSON.stringify(fixtureJson);

function bearer(key = KEY): HeadersInit {
  return { Authorization: `Bearer ${key}` };
}

async function postBundle(body: unknown, key = KEY): Promise<Response> {
  return SELF.fetch(BUNDLE_URL, {
    method: "POST",
    headers: { ...bearer(key), "content-type": "application/json" },
    body: typeof body === "string" ? body : JSON.stringify(body),
  });
}

describe("Worker /api/league/bundle", () => {
  beforeEach(async () => {
    await env.BOARD.put(BOARD_KEY, JSON.stringify(boardFixture));
    await env.BOARD.delete(LEAGUE_BUNDLE_KEY);
    await env.DB.batch([
      env.DB.prepare("DELETE FROM picks WHERE draft_id = 1"),
      env.DB.prepare("DELETE FROM teams WHERE draft_id = 1"),
      env.DB.prepare("DELETE FROM drafts WHERE id = 1"),
    ]);
  });

  it("requires the tracker bearer key", async () => {
    expect((await SELF.fetch(BUNDLE_URL)).status).toBe(401);
    expect((await postBundle(fixtureJson, "nope")).status).toBe(401);
    expect(await env.BOARD.get(LEAGUE_BUNDLE_KEY)).toBeNull();
  });

  it("returns 404 when no bundle has been accepted", async () => {
    const res = await SELF.fetch(BUNDLE_URL, { headers: bearer() });
    expect(res.status).toBe(404);
    expect(await res.json()).toEqual({ error: "no league bundle" });
  });

  it("accepts a valid LeagueBundle, stores it in KV, and does not echo the body", async () => {
    const posted = await postBundle(fixtureJson);
    expect(posted.status).toBe(200);
    expect(posted.headers.get("content-type")).toContain("application/json");
    const summary = await posted.json();
    expect(summary).toEqual({
      ok: true,
      season: 2024,
      current_week: 1,
      teams: 1,
      players: 0,
      source: "fixture",
      synced_at: "2026-07-22T12:00:00Z",
    });
    expect(JSON.stringify(summary)).not.toContain("Brian");
    expect(JSON.stringify(summary)).not.toContain("mock-1");

    const stored = await env.BOARD.get(LEAGUE_BUNDLE_KEY);
    expect(stored).not.toBeNull();
    expect(JSON.parse(stored!)).toEqual(fixtureJson);

    const fetched = await SELF.fetch(BUNDLE_URL, { headers: bearer() });
    expect(fetched.status).toBe(200);
    expect(await fetched.text()).toBe(stored);
    expect(await env.BOARD.get(BOARD_KEY)).toBe(JSON.stringify(boardFixture));
  });

  it("rejects extra keys and incomplete rosters without replacing a stored bundle", async () => {
    expect((await postBundle(fixtureJson)).status).toBe(200);
    const previous = await env.BOARD.get(LEAGUE_BUNDLE_KEY);

    const extra = await postBundle({ ...fixtureJson, extra: "typo" });
    expect(extra.status).toBe(400);
    const extraBody = await extra.json();
    expect(extraBody).toEqual({
      error: "invalid_bundle",
      message: "bundle has unknown or missing fields",
    });
    expect(JSON.stringify(extraBody)).not.toContain("typo");

    const incomplete = await postBundle({ ...fixtureJson, rosters: [] });
    expect(incomplete.status).toBe(400);
    expect(await incomplete.json()).toMatchObject({
      error: "invalid_bundle",
      message: expect.stringMatching(/every team/),
    });

    expect(await env.BOARD.get(LEAGUE_BUNDLE_KEY)).toBe(previous);
    expect(await env.BOARD.get(BOARD_KEY)).toBe(JSON.stringify(boardFixture));
  });

  it("rejects invalid JSON without logging-shaped echo", async () => {
    const res = await postBundle("{not-json");
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({
      error: "invalid_json",
      message: "body must be JSON",
    });
    expect(await env.BOARD.get(LEAGUE_BUNDLE_KEY)).toBeNull();
  });

  it("does not read or write live draft tables", async () => {
    const before = await env.DB.prepare("SELECT COUNT(*) AS n FROM drafts").first<{ n: number }>();
    expect((await postBundle(fixtureJson)).status).toBe(200);
    const after = await env.DB.prepare("SELECT COUNT(*) AS n FROM drafts").first<{ n: number }>();
    expect(after).toEqual(before);
    expect((await SELF.fetch("https://x/api/draft", { headers: bearer() })).status).toBe(200);
    expect(await (await SELF.fetch("https://x/api/draft", { headers: bearer() })).json()).toEqual({
      configured: false,
      picks: [],
      revision: 0,
    });
  });

  it("returns Allow: GET, POST for other methods", async () => {
    const res = await SELF.fetch(BUNDLE_URL, { method: "PUT", headers: bearer() });
    expect(res.status).toBe(405);
    expect(res.headers.get("allow")).toBe("GET, POST");
  });
});
