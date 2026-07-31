import { describe, expect, it } from "vitest";
import { requestJson } from "../src/request-json";

describe("JSON API requests", () => {
  it("turns a rejected network request into a recoverable result", async () => {
    const result = await requestJson(
      () => Promise.reject(new TypeError("device is offline")),
      "/api/mocks",
    );

    expect(result).toEqual({
      response: null,
      value: null,
      transportError: "Unable to reach the tracker. Check your connection and try again.",
    });
  });
});
