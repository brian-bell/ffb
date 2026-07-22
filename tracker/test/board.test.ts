import { describe, it, expect, beforeEach } from "vitest";
import { env } from "cloudflare:test";
import { getBoardText, validateVersion, BOARD_KEY, BOARD_VERSION } from "../src/board";

declare module "cloudflare:test" {
  interface ProvidedEnv {
    BOARD: KVNamespace;
    TRACKER_API_KEY: string;
  }
}

describe("getBoardText", () => {
  beforeEach(async () => {
    await env.BOARD.delete(BOARD_KEY);
  });

  it("returns null on a KV miss (nothing published yet)", async () => {
    expect(await getBoardText(env)).toBeNull();
  });

  it("passes the stored text straight through (no parse/reshape)", async () => {
    const raw = '{"version":1,"players":[]}';
    await env.BOARD.put(BOARD_KEY, raw);
    expect(await getBoardText(env)).toBe(raw);
  });
});

describe("validateVersion", () => {
  it("accepts the pinned version", () => {
    expect(validateVersion({ version: BOARD_VERSION })).toBe(true);
  });

  it("rejects a different version (contract drift → loud degrade)", () => {
    expect(validateVersion({ version: 2 })).toBe(false);
  });

  it("rejects a missing / non-numeric version", () => {
    expect(validateVersion({})).toBe(false);
    expect(validateVersion({ version: "1" })).toBe(false);
  });
});
