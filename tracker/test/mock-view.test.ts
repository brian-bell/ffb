import { describe, expect, it } from "vitest";
import { initialBoardView, LIST_CHUNK, nextBoardView } from "../src/board-view";
import { reconcileMockBoardView } from "../src/mock-view";

describe("mock player list view", () => {
  it("grows by one shared chunk and resets when the position changes", () => {
    const grown = nextBoardView(initialBoardView, { type: "loadMore" });
    expect(grown.visibleLimit).toBe(LIST_CHUNK * 2);

    expect(nextBoardView(grown, { type: "selectPosition", position: "RB" })).toEqual({
      ...initialBoardView, position: "RB", visibleLimit: LIST_CHUNK,
    });
  });

  it("clears a selected player that became unavailable after a reload", () => {
    expect(
      reconcileMockBoardView({ ...initialBoardView, selectedKey: "drafted" }, "mock-1", "mock-1", [
        { key: "available" },
      ]),
    ).toEqual({ ...initialBoardView, selectedKey: null });
  });

  it("clears an otherwise available selection when the mock changes", () => {
    expect(
      reconcileMockBoardView({ ...initialBoardView, position: "RB", selectedKey: "available" }, "mock-1", "mock-2", [
        { key: "available" },
      ]),
    ).toEqual(initialBoardView);
  });

  it("retains an available selection while reloading the same mock", () => {
    expect(
      reconcileMockBoardView({ ...initialBoardView, position: "RB", selectedKey: "available" }, "mock-1", "mock-1", [
        { key: "available" },
      ]),
    ).toEqual({ ...initialBoardView, position: "RB", selectedKey: "available" });
  });

});
