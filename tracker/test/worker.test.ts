import { describe, it, expect, beforeAll } from "vitest";
import { SELF, env } from "cloudflare:test";
import { BOARD_KEY } from "../src/board";
import fixtureJson from "./fixtures/board.json";

declare module "cloudflare:test" {
  interface ProvidedEnv {
    BOARD: KVNamespace;
    TRACKER_API_KEY: string;
  }
}

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
