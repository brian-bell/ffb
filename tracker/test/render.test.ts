import { describe, it, expect } from "vitest";
import { renderBoard } from "../src/render";
import type { Board } from "../src/types";
import fixture from "./fixtures/board.json";

const board = fixture as unknown as Board;

/** Pull the single `.rowA` markup block for a player by (escaped) name. */
function rowFor(html: string, name: string): string {
  const rows = html.split('<div class="rowA">').slice(1);
  const hit = rows.find((r) => r.includes(`<b>${name}</b>`));
  if (!hit) throw new Error(`no row for ${name}`);
  return hit;
}

describe("renderBoard — ALL view", () => {
  const html = renderBoard(board, "ALL");

  it("renders every player as a row", () => {
    expect(html.match(/class="rowA"/g)).toHaveLength(board.players.length);
  });

  it("shows no tier dividers in the mixed view (per-row tier chip instead)", () => {
    expect(html).not.toContain('class="trule"');
    // a scored row carries its tier inline (e.g. "· T1")
    expect(rowFor(html, "Christian McCaffrey")).toContain("T1");
  });
});

describe("renderBoard — position view groups by tier", () => {
  const html = renderBoard(board, "RB");

  it("shows only the requested position", () => {
    expect(html.match(/class="rowA"/g)).toHaveLength(4); // CMC, Bijan, Saquon, Taylor
    expect(html).not.toContain("Ja'Marr Chase");
  });

  it("emits ascending tier dividers labelled by position", () => {
    expect(html).toContain("Tier 1 · RB");
    expect(html).toContain("Tier 2 · RB");
    expect(html).toContain("Tier 3 · RB");
    // dividers appear in ascending order
    expect(html.indexOf("Tier 1 · RB")).toBeLessThan(html.indexOf("Tier 2 · RB"));
    expect(html.indexOf("Tier 2 · RB")).toBeLessThan(html.indexOf("Tier 3 · RB"));
  });

  it("keeps rows in pos_rank order", () => {
    expect(html.indexOf("Christian McCaffrey")).toBeLessThan(html.indexOf("Bijan Robinson"));
    expect(html.indexOf("Bijan Robinson")).toBeLessThan(html.indexOf("Saquon Barkley"));
    expect(html.indexOf("Saquon Barkley")).toBeLessThan(html.indexOf("Jonathan Taylor"));
  });
});

describe("renderBoard — +/- value chip", () => {
  const html = renderBoard(board, "ALL");

  it("is green (up) when the market drafts him later than we rank him", () => {
    const row = rowFor(html, "Josh Allen"); // adp_rank 9 - rank 7 = +2
    expect(row).toContain('class="chip up"');
    expect(row).toContain("+2");
  });

  it("is red (dn) on a reach", () => {
    const row = rowFor(html, "Garrett Wilson"); // adp_rank 7 - rank 8 = -1
    expect(row).toContain('class="chip dn"');
    expect(row).toContain("-1");
  });

  it("is neutral (zero) at parity", () => {
    const row = rowFor(html, "Christian McCaffrey"); // 1 - 1 = 0
    expect(row).toContain('class="chip zero"');
  });
});

describe("renderBoard — ADP-only and null fields", () => {
  const html = renderBoard(board, "DEF");

  it("renders a null adp as an em dash", () => {
    const row = rowFor(html, "New York Jets"); // has vorp, adp null
    expect(row).toContain('class="num adp tnum na"');
    expect(row).toContain("—");
  });

  it("renders an ADP-only row (null points/vorp) with a dashed VORP and no bar", () => {
    const row = rowFor(html, "San Francisco Defense");
    expect(row).toContain('class="num vorp na"');
    expect(row).not.toContain('class="bar"');
    // but its ADP is present
    expect(row).toContain("108.0");
  });

  it("gives rows with no computable value a neutral (na) chip, not a false signal", () => {
    expect(rowFor(html, "New York Jets")).toContain('class="chip na"');
    expect(rowFor(html, "San Francisco Defense")).toContain('class="chip na"');
  });
});

describe("renderBoard — VORP micro-bar magnitude", () => {
  it("scales the bar to the board-wide max VORP (leader = full width)", () => {
    const html = renderBoard(board, "ALL");
    // McCaffrey is the max VORP (120) → 100%
    expect(rowFor(html, "Christian McCaffrey")).toContain("--w:100%");
  });
});

describe("renderBoard — draft availability", () => {
  it("omits picked player keys by default and renders annotated history on request", () => {
    const picked = new Map([["k0", { overall_pick: 1, team_name: "Brian" }]]);
    const available = renderBoard(board, "ALL", { picked });
    expect(available).not.toContain("Christian McCaffrey");

    const history = renderBoard(board, "ALL", { picked, mode: "drafted" });
    expect(history).toContain("Christian McCaffrey");
    expect(history).toContain("1.01 · Brian");
    expect(history).not.toContain("Ja'Marr Chase");
  });
});
