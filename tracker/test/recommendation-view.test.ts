import { describe, expect, it } from "vitest";
import { needsHtml, recommendationHtml } from "../src/recommendation-view";
import type { RecommendationState } from "../src/recommendation";

const state = {
  context: { roster: { openDedicated: { RB: 1 }, openFlex: 1 }, nextUserOverallPick: 12, picksUntilNextTurn: 3, remainingUserPicks: 10 },
  recommendation: { player: { key: "p1", name: "Bijan <Robinson>", vorp: null }, position: "RB", need: "dedicated", forced: false, tier: null, tierRemaining: null, tierAtRisk: false, nextTierVorpDrop: null, marketUrgent: null, reason: "Fills your RB starter." },
  warnings: [],
} as unknown as RecommendationState;

describe("recommendation view", () => {
  it("renders useful labels and safe null placeholders", () => {
    expect(recommendationHtml(state)).toContain("YOUR PICK");
    expect(recommendationHtml(state)).toContain("UNTIERED · — VORP");
    expect(recommendationHtml(state)).toContain("Bijan &lt;Robinson&gt;");
    expect(needsHtml(state)).toContain("RB");
    expect(needsHtml(state)).toContain("FLEX");
  });
});
