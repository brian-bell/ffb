import { describe, expect, it } from "vitest";
import { describeBoardView, initialBoardView, nextBoardView } from "../src/board-view";

describe("board view state", () => {
  it("starts with every available player and collapsed pick tools", () => {
    expect(initialBoardView).toEqual({
      position: "ALL",
      mode: "available",
      pickToolsExpanded: false,
    });
  });

  it("selects a position without changing the other view choices", () => {
    expect(nextBoardView(initialBoardView, { type: "selectPosition", position: "RB" })).toEqual({
      position: "RB",
      mode: "available",
      pickToolsExpanded: false,
    });
  });

  it("selects Drafted without resetting the position", () => {
    const rbView = nextBoardView(initialBoardView, { type: "selectPosition", position: "RB" });

    expect(nextBoardView(rbView, { type: "selectMode", mode: "drafted" })).toEqual({
      position: "RB",
      mode: "drafted",
      pickToolsExpanded: false,
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
    });
  });

  it("expands collapsed pick tools", () => {
    expect(nextBoardView(initialBoardView, { type: "togglePickTools" })).toEqual({
      ...initialBoardView,
      pickToolsExpanded: true,
    });
  });

  it("collapses expanded pick tools on a second toggle", () => {
    const expanded = nextBoardView(initialBoardView, { type: "togglePickTools" });

    expect(nextBoardView(expanded, { type: "togglePickTools" })).toEqual(initialBoardView);
  });

  it("collapses pick tools after a pick is recorded", () => {
    const expanded = nextBoardView(initialBoardView, { type: "togglePickTools" });

    expect(nextBoardView(expanded, { type: "pickRecorded" })).toEqual(initialBoardView);
  });

  it("preserves expanded pick tools when position or mode changes", () => {
    const expanded = nextBoardView(initialBoardView, { type: "togglePickTools" });
    const rbView = nextBoardView(expanded, { type: "selectPosition", position: "RB" });
    const history = nextBoardView(rbView, { type: "selectMode", mode: "drafted" });

    expect(rbView.pickToolsExpanded).toBe(true);
    expect(history.pickToolsExpanded).toBe(true);
  });
});

describe("board view description", () => {
  it("explains the overall available board order", () => {
    expect(describeBoardView(initialBoardView)).toEqual({
      lead: "Overall board",
      detail: "Tier badges stay inline. Choose a position to group by tier.",
      orderLabel: "BOARD ORDER",
    });
  });

  it("explains positional tier order for available players", () => {
    const rbView = nextBoardView(initialBoardView, { type: "selectPosition", position: "RB" });

    expect(describeBoardView(rbView)).toEqual({
      lead: "RB tiers",
      detail: "Available RBs are grouped by positional tier.",
      orderLabel: "TIER ORDER",
    });
  });

  it("explains chronological order for all drafted players", () => {
    const history = nextBoardView(initialBoardView, { type: "selectMode", mode: "drafted" });

    expect(describeBoardView(history)).toEqual({
      lead: "Draft log",
      detail: "All recorded picks are in chronological order; tiers stay inline.",
      orderLabel: "PICK ORDER",
    });
  });

  it("explains that filtered draft history is not regrouped", () => {
    const history = nextBoardView(
      nextBoardView(initialBoardView, { type: "selectPosition", position: "RB" }),
      { type: "selectMode", mode: "drafted" },
    );

    expect(describeBoardView(history)).toEqual({
      lead: "RB draft log",
      detail: "Filtered RB history stays chronological; no tier regrouping.",
      orderLabel: "PICK ORDER",
    });
  });
});
