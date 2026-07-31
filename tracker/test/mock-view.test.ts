import { describe, expect, it } from "vitest";
import { LIST_CHUNK } from "../src/board-view";
import {
  initialMockView,
  nextMockView,
  reconcileMockSelection,
} from "../src/mock-view";

describe("mock player list view", () => {
  it("grows by one shared chunk and resets when the position changes", () => {
    const grown = nextMockView(initialMockView, { type: "loadMore" });
    expect(grown.visibleLimit).toBe(LIST_CHUNK * 2);

    expect(nextMockView(grown, { type: "selectPosition", position: "RB" })).toEqual({
      position: "RB",
      visibleLimit: LIST_CHUNK,
    });
  });

  it("clears a selected player that became unavailable after a reload", () => {
    expect(
      reconcileMockSelection("drafted", "mock-1", "mock-1", [
        { key: "available" },
      ]),
    ).toBeNull();
  });

  it("clears an otherwise available selection when the mock changes", () => {
    expect(
      reconcileMockSelection("available", "mock-1", "mock-2", [
        { key: "available" },
      ]),
    ).toBeNull();
  });

  it("retains an available selection while reloading the same mock", () => {
    expect(
      reconcileMockSelection("available", "mock-1", "mock-1", [
        { key: "available" },
      ]),
    ).toBe("available");
  });

});
