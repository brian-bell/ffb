import { describe, expect, it } from "vitest";
import { LIST_CHUNK } from "../src/board-view";
import { initialMockView, nextMockView } from "../src/mock-view";

describe("mock player list view", () => {
  it("grows by one shared chunk and resets when the position changes", () => {
    const grown = nextMockView(initialMockView, { type: "loadMore" });
    expect(grown.visibleLimit).toBe(LIST_CHUNK * 2);

    expect(nextMockView(grown, { type: "selectPosition", position: "RB" })).toEqual({
      position: "RB",
      visibleLimit: LIST_CHUNK,
    });
  });
});
