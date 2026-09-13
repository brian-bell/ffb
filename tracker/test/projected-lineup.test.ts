import { expect, it } from "vitest";
import { projectedLineup } from "../src/roster-fit";

it("matches exhaustive lineup search across dedicated and overlapping flex slots", () => {
  const eligible: Record<string, string[]> = { RB: ["RB"], WR: ["WR"], TE: ["TE"], "W/T": ["WR", "TE"], "W/R/T": ["RB", "WR", "TE"] };
  const shapes = [["WR", "W/T", "W/R/T"], ["W/R/T", "W/T", "TE"], ["RB", "RB", "W/T"], ["WR", "TE"]];
  for (const slots of shapes) {
    const counts: Record<string, number> = { BN: 3 };
    for (const slot of slots) counts[slot] = (counts[slot] ?? 0) + 1;
    for (let scenario = 0; scenario < 81; scenario++) {
      const players = Array.from({ length: 4 }, (_, i) => ({ key: `${i}`, pos: ["RB", "WR", "TE"][Math.floor(scenario / 3 ** i) % 3]!, points: [200, 150, 90, 10][i]! }));
      const best = (slot: number, used: Set<number>): number => slot === slots.length ? 0 : Math.max(
        best(slot + 1, used), ...players.flatMap((p, i) => !used.has(i) && eligible[slots[slot]!]!.includes(p.pos)
          ? [p.points + best(slot + 1, new Set([...used, i]))] : []));
      const actual = projectedLineup(counts, players);
      expect(actual.total).toBe(best(0, new Set()));
      expect(actual.assignments.size).toBeLessThanOrEqual(slots.length);
    }
  }
});
