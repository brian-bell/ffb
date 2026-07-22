import { describe, it, expect } from "vitest";
import { requireBearer } from "../src/auth";

const env = { TRACKER_API_KEY: "s3cret-key" };

function req(headers: Record<string, string> = {}): Request {
  return new Request("https://x/api/board", { headers });
}

describe("requireBearer", () => {
  it("rejects a request with no Authorization header (401)", () => {
    const res = requireBearer(req(), env);
    expect(res).not.toBeNull();
    expect(res!.status).toBe(401);
  });

  it("rejects a malformed / non-Bearer scheme (401)", () => {
    expect(requireBearer(req({ Authorization: "Basic abc" }), env)!.status).toBe(401);
    expect(requireBearer(req({ Authorization: "s3cret-key" }), env)!.status).toBe(401);
    expect(requireBearer(req({ Authorization: "Bearer" }), env)!.status).toBe(401);
  });

  it("rejects a wrong key (401)", () => {
    expect(requireBearer(req({ Authorization: "Bearer wrong" }), env)!.status).toBe(401);
  });

  it("rejects a key that is a prefix of the real key (401)", () => {
    expect(requireBearer(req({ Authorization: "Bearer s3cret" }), env)!.status).toBe(401);
  });

  it("passes a correct key (returns null)", () => {
    expect(requireBearer(req({ Authorization: "Bearer s3cret-key" }), env)).toBeNull();
  });

  it("401 body is small JSON with a content-type", async () => {
    const res = requireBearer(req(), env)!;
    expect(res.headers.get("content-type")).toContain("application/json");
    expect(await res.json()).toEqual({ error: "unauthorized" });
  });
});
