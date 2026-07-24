import { describe, expect, it } from "vitest";
import { initialBoardView, LIST_CHUNK, nextBoardView } from "../src/board-view";

describe("board view state", () => {
  it("starts with every available player and collapsed pick tools", () => {
    expect(initialBoardView).toEqual({
      position: "ALL",
      mode: "available",
      pickToolsExpanded: false,
      searchQuery: "",
      selectedKey: null,
      visibleLimit: LIST_CHUNK,
    });
  });

  it("selects a position without changing the other view choices", () => {
    expect(nextBoardView(initialBoardView, { type: "selectPosition", position: "RB" })).toEqual({
      position: "RB",
      mode: "available",
      pickToolsExpanded: false,
      searchQuery: "",
      selectedKey: null,
      visibleLimit: LIST_CHUNK,
    });
  });

  it("selects Drafted without resetting the position", () => {
    const rbView = nextBoardView(initialBoardView, { type: "selectPosition", position: "RB" });

    expect(nextBoardView(rbView, { type: "selectMode", mode: "drafted" })).toEqual({
      position: "RB",
      mode: "drafted",
      pickToolsExpanded: false,
      searchQuery: "",
      selectedKey: null,
      visibleLimit: LIST_CHUNK,
    });
  });

  it("returns to Available without resetting the position", () => {
    const rbHistory = nextBoardView(
      nextBoardView(initialBoardView, { type: "selectPosition", position: "RB" }),
      { type: "selectMode", mode: "drafted" },
    );

    expect(nextBoardView(rbHistory, { type: "selectMode", mode: "available" })).toEqual({
      position: "RB",
      mode: "available",
      pickToolsExpanded: false,
      searchQuery: "",
      selectedKey: null,
      visibleLimit: LIST_CHUNK,
    });
  });

  it("expands collapsed pick tools", () => {
    expect(nextBoardView(initialBoardView, { type: "togglePickTools" })).toEqual({
      ...initialBoardView,
      pickToolsExpanded: true,
    });
  });

  it("selects a player and expands pick tools, then clears a repeated selection without collapsing", () => {
    const selected = nextBoardView(initialBoardView, { type: "playerSelected", key: "k0" });

    expect(selected).toEqual({ ...initialBoardView, selectedKey: "k0", pickToolsExpanded: true });
    expect(nextBoardView(selected, { type: "playerSelected", key: "k0" })).toEqual({
      ...initialBoardView,
      pickToolsExpanded: true,
    });
  });

  it("clears an explicit selection without collapsing pick tools", () => {
    const selected = nextBoardView(initialBoardView, { type: "playerSelected", key: "k0" });

    expect(nextBoardView(selected, { type: "selectionCleared" })).toEqual({
      ...initialBoardView,
      pickToolsExpanded: true,
    });
  });

  it("keeps the underlying mode and position untouched while search starts and clears", () => {
    const draftedRb = nextBoardView(
      nextBoardView(initialBoardView, { type: "selectPosition", position: "RB" }),
      { type: "selectMode", mode: "drafted" },
    );
    const searching = nextBoardView(draftedRb, { type: "searchChanged", query: "allen" });

    expect(searching).toEqual({ ...draftedRb, searchQuery: "allen" });
    expect(nextBoardView(searching, { type: "searchChanged", query: "" })).toEqual(draftedRb);
  });

  it("collapses expanded pick tools on a second toggle", () => {
    const expanded = nextBoardView(initialBoardView, { type: "togglePickTools" });

    expect(nextBoardView(expanded, { type: "togglePickTools" })).toEqual(initialBoardView);
  });

  it("collapses pick tools after a pick is recorded", () => {
    const expanded = nextBoardView(initialBoardView, { type: "togglePickTools" });

    expect(nextBoardView(expanded, { type: "pickRecorded" })).toEqual(initialBoardView);
  });

  it("clears search and selection after recording without changing board filters", () => {
    const rbHistory = nextBoardView(
      nextBoardView(initialBoardView, { type: "selectPosition", position: "RB" }),
      { type: "selectMode", mode: "drafted" },
    );
    const searching = nextBoardView(
      nextBoardView(rbHistory, { type: "searchChanged", query: "allen" }),
      { type: "playerSelected", key: "k2" },
    );

    expect(nextBoardView(searching, { type: "pickRecorded" })).toEqual(rbHistory);
  });

  it("starts the list at one chunk and grows it a chunk per loadMore", () => {
    expect(Number.isInteger(LIST_CHUNK)).toBe(true);
    expect(LIST_CHUNK).toBeGreaterThan(0);
    expect(initialBoardView.visibleLimit).toBe(LIST_CHUNK);

    const grown = nextBoardView(initialBoardView, { type: "loadMore" });
    expect(grown.visibleLimit).toBe(LIST_CHUNK * 2);
    expect(nextBoardView(grown, { type: "loadMore" }).visibleLimit).toBe(LIST_CHUNK * 3);
  });

  it("resets a grown list whenever the visible list changes meaning", () => {
    const grown = nextBoardView(initialBoardView, { type: "loadMore" });
    const resets = [
      { type: "selectPosition", position: "RB" },
      { type: "selectMode", mode: "drafted" },
      { type: "searchChanged", query: "allen" },
    ] as const;

    for (const event of resets) {
      expect(nextBoardView(grown, event).visibleLimit).toBe(LIST_CHUNK);
    }
  });

  it("keeps a grown list grown across selection, pick-tool, and pick events", () => {
    // pickRecorded must preserve the limit: the recorded-pick fast path edits
    // the grown DOM in place without a re-render, so resetting state here
    // would collapse the list on the next loadMore.
    const grown = nextBoardView(initialBoardView, { type: "loadMore" });
    const preserves = [
      { type: "playerSelected", key: "k0" },
      { type: "selectionCleared" },
      { type: "togglePickTools" },
      { type: "pickRecorded" },
    ] as const;

    for (const event of preserves) {
      expect(nextBoardView(grown, event).visibleLimit).toBe(LIST_CHUNK * 2);
    }
  });

  it("preserves expanded pick tools when position or mode changes", () => {
    const expanded = nextBoardView(initialBoardView, { type: "togglePickTools" });
    const rbView = nextBoardView(expanded, { type: "selectPosition", position: "RB" });
    const history = nextBoardView(rbView, { type: "selectMode", mode: "drafted" });

    expect(rbView.pickToolsExpanded).toBe(true);
    expect(history.pickToolsExpanded).toBe(true);
  });
});
