import { describe, it, expect, beforeAll, beforeEach } from "vitest";
import { SELF, env } from "cloudflare:test";
import { BOARD_KEY } from "../src/board";
import fixtureJson from "./fixtures/board.json";

const KEY = "test-secret-key"; // matches vitest.config.ts miniflare binding
const fixtureText = JSON.stringify(fixtureJson);

function bearer(key: string): HeadersInit {
  return { Authorization: `Bearer ${key}` };
}

describe("Worker /api/health", () => {
  it("is a public 200 liveness ping", async () => {
    const res = await SELF.fetch("https://x/api/health");
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true });
  });
});

describe("Worker /api/board auth", () => {
  beforeAll(async () => {
    await env.BOARD.put(BOARD_KEY, fixtureText);
  });

  it("rejects a missing key (401)", async () => {
    expect((await SELF.fetch("https://x/api/board")).status).toBe(401);
  });

  it("rejects a wrong key (401)", async () => {
    const res = await SELF.fetch("https://x/api/board", { headers: bearer("nope") });
    expect(res.status).toBe(401);
  });

  it("serves the KV board verbatim to a valid key (200)", async () => {
    const res = await SELF.fetch("https://x/api/board", { headers: bearer(KEY) });
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toContain("application/json");
    expect(await res.text()).toBe(fixtureText);
  });
});

describe("Worker /api/board when nothing is published", () => {
  it("returns 404 so the frontend can prompt a publish", async () => {
    await env.BOARD.delete(BOARD_KEY);
    const res = await SELF.fetch("https://x/api/board", { headers: bearer(KEY) });
    expect(res.status).toBe(404);
  });
});

describe("Worker draft state", () => {
  beforeEach(async () => {
    await env.DB.batch([
      env.DB.prepare("DELETE FROM picks WHERE draft_id = 1"),
      env.DB.prepare("DELETE FROM teams WHERE draft_id = 1"),
      env.DB.prepare("DELETE FROM drafts WHERE id = 1"),
    ]);
    await env.BOARD.put(BOARD_KEY, fixtureText);
  });

  it("returns an authenticated unconfigured draft state", async () => {
    const res = await SELF.fetch("https://x/api/draft", { headers: bearer(KEY) });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ configured: false, picks: [], revision: 0 });
  });

  it("configures, records, and undoes a canonical board player", async () => {
    const config = await SELF.fetch("https://x/api/draft", {
      method: "PUT",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({
        name: "Home League",
        rounds: 2,
        teams: [
          { name: "Brian", is_user: true },
          { name: "Other", is_user: false },
        ],
      }),
    });
    expect(config.status).toBe(200);
    const configured = (await config.json()) as { next: { overall_pick: number } };
    expect(configured.next.overall_pick).toBe(1);

    const pick = await SELF.fetch("https://x/api/picks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ player_key: fixtureJson.players[0].key, expected_overall_pick: 1 }),
    });
    expect(pick.status).toBe(201);
    expect(await pick.json()).toMatchObject({ picks: [{ player_key: fixtureJson.players[0].key }], next: { overall_pick: 2, team_name: "Other" } });

    const undo = await SELF.fetch("https://x/api/picks/latest", {
      method: "DELETE",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ expected_overall_pick: 1 }),
    });
    expect(undo.status).toBe(200);
    expect(await undo.json()).toMatchObject({ picks: [], next: { overall_pick: 1 } });
  });

  it("uses stable conflict and validation errors instead of mutating stale state", async () => {
    const invalid = await SELF.fetch("https://x/api/draft", {
      method: "PUT",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ rounds: 16, teams: [{ name: "Only team", is_user: true }] }),
    });
    expect(invalid.status).toBe(400);
    expect(await invalid.json()).toMatchObject({ error: "invalid_draft" });

    await SELF.fetch("https://x/api/draft", {
      method: "PUT",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ rounds: 1, teams: [{ name: "Brian", is_user: true }, { name: "Other", is_user: false }] }),
    });
    const unknown = await SELF.fetch("https://x/api/picks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ player_key: "not-on-board", expected_overall_pick: 1 }),
    });
    expect(unknown.status).toBe(422);
    expect(await unknown.json()).toMatchObject({ error: "unknown_player" });

    const staleUndo = await SELF.fetch("https://x/api/picks/latest", {
      method: "DELETE",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ expected_overall_pick: 1 }),
    });
    expect(staleUndo.status).toBe(409);
    expect(await staleUndo.json()).toMatchObject({ error: "no_picks" });
  });

  it("rejects an unconfigured draft before attempting to read the board", async () => {
    await env.BOARD.delete(BOARD_KEY);
    const response = await SELF.fetch("https://x/api/picks", {
      method: "POST",
      headers: { ...bearer(KEY), "content-type": "application/json" },
      body: JSON.stringify({ player_key: "any", expected_overall_pick: 1 }),
    });
    expect(response.status).toBe(409);
    expect(await response.json()).toMatchObject({ error: "draft_unconfigured" });
  });
});

describe("Worker static shell", () => {
  it("serves the HTML shell publicly at /", async () => {
    const res = await SELF.fetch("https://x/");
    expect(res.status).toBe(200);
    const body = await res.text();
    expect(body).toContain("DRAFT");
    expect(body).toContain('data-list');
  });

  it("404s an unknown /api path", async () => {
    const res = await SELF.fetch("https://x/api/nope", { headers: bearer(KEY) });
    expect(res.status).toBe(404);
  });
});
