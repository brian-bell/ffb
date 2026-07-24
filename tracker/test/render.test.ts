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
    expect(rowFor(html, "Christian McCaffrey")).toContain('<span class="tier-chip">T1</span>');
  });

  it("labels ADP-only players with an explicit tier chip", () => {
    expect(rowFor(html, "San Francisco Defense")).toContain('<span class="tier-chip">ADP</span>');
  });

  it("keeps players in overall board rank order", () => {
    expect(html.indexOf("Christian McCaffrey")).toBeLessThan(html.indexOf("Ja'Marr Chase"));
    expect(html.indexOf("Ja'Marr Chase")).toBeLessThan(html.indexOf("Josh Allen"));
  });
});

describe("renderBoard — position view groups by tier", () => {
  const picked = new Map<string, { overall_pick: number; round: number; round_pick: number; team_name: string }>();
  const html = renderBoard(board, "RB", { picked });

  it("shows only the requested position", () => {
    expect(html.match(/class="rowA"/g)).toHaveLength(4); // CMC, Bijan, Saquon, Taylor
    expect(html).not.toContain("Ja'Marr Chase");
  });

  it("emits ascending tier dividers labelled by position", () => {
    expect(html).toContain("Tier 1 · RB · 2 left");
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

  it("counts only visible, undrafted players in each tier", () => {
    const oneDrafted = new Map([
      ["k0", { overall_pick: 1, round: 1, round_pick: 1, team_name: "Brian" }],
    ]);

    expect(renderBoard(board, "RB", { picked: oneDrafted })).toContain("Tier 1 · RB · 1 left");
  });

  it("omits a tier heading when every player in that tier is drafted", () => {
    const tierDrafted = new Map([
      ["k0", { overall_pick: 1, round: 1, round_pick: 1, team_name: "Brian" }],
      ["k3", { overall_pick: 2, round: 1, round_pick: 2, team_name: "Opponent" }],
    ]);
    const available = renderBoard(board, "RB", { picked: tierDrafted });

    expect(available).not.toContain("Tier 1 · RB");
    expect(available).toContain("Tier 2 · RB · 1 left");
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

  it("labels the untiered positional group and counts its available players", () => {
    expect(html).toContain("ADP only · DEF · 1 left");
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
  it("renders selectable available rows as buttons with stable player keys and pressed state", () => {
    const html = renderBoard(board, "ALL", { selectable: true, selectedKey: "k0" });

    expect(html).toContain('<button type="button" class="rowA selectable selected" data-player-key="k0" aria-pressed="true">');
    expect(html).toContain('<button type="button" class="rowA selectable" data-player-key="k1" aria-pressed="false">');
  });

  it("omits picked player keys by default and renders annotated history on request", () => {
    const picked = new Map([["k0", { overall_pick: 1, round: 1, round_pick: 1, team_name: "Brian" }]]);
    const available = renderBoard(board, "ALL", { picked });
    expect(available).not.toContain("Christian McCaffrey");

    const history = renderBoard(board, "ALL", { picked, mode: "drafted" });
    expect(history).toContain("Christian McCaffrey");
    expect(history).toContain("1.01 · Brian");
    expect(history).not.toContain("Ja'Marr Chase");
  });

  it("uses the persisted round snapshot rather than deriving it from overall pick", () => {
    const picked = new Map([["k0", { overall_pick: 13, round: 2, round_pick: 1, team_name: "Brian" }]]);
    const history = renderBoard(board, "ALL", { picked, mode: "drafted" });
    expect(history).toContain("2.01 · Brian");
    expect(history).not.toContain("13.13");
  });

  it("keeps drafted rows read-only even when selectable rendering is requested", () => {
    const picked = new Map([["k0", { overall_pick: 1, round: 1, round_pick: 1, team_name: "Brian" }]]);
    const history = renderBoard(board, "ALL", { picked, mode: "drafted", selectable: true, selectedKey: "k0" });

    expect(history).toContain('<div class="rowA drafted">');
    expect(history).not.toContain("data-player-key");
    expect(history).not.toContain("<button");
  });

  it("renders Drafted + ALL in chronological pick order with inline tiers", () => {
    const picked = new Map([
      ["k3", { overall_pick: 1, round: 1, round_pick: 1, team_name: "Brian" }],
      ["k0", { overall_pick: 2, round: 1, round_pick: 2, team_name: "Opponent" }],
    ]);
    const history = renderBoard(board, "ALL", { picked, mode: "drafted" });

    expect(history.indexOf("Bijan Robinson")).toBeLessThan(history.indexOf("Christian McCaffrey"));
    expect(history.match(/class="tier-chip">T1/g)).toHaveLength(2);
  });

  it("filters Drafted by position without changing chronological pick order", () => {
    const picked = new Map([
      ["k8", { overall_pick: 1, round: 1, round_pick: 1, team_name: "Brian" }],
      ["k1", { overall_pick: 2, round: 1, round_pick: 2, team_name: "Opponent" }],
      ["k0", { overall_pick: 3, round: 1, round_pick: 3, team_name: "Another" }],
    ]);
    const history = renderBoard(board, "RB", { picked, mode: "drafted" });

    expect(history).not.toContain("Ja'Marr Chase");
    expect(history.indexOf("Jonathan Taylor")).toBeLessThan(history.indexOf("Christian McCaffrey"));
  });

  it("never inserts tier dividers into filtered Drafted history", () => {
    const picked = new Map([
      ["k0", { overall_pick: 1, round: 1, round_pick: 1, team_name: "Brian" }],
      ["k5", { overall_pick: 2, round: 1, round_pick: 2, team_name: "Opponent" }],
      ["k3", { overall_pick: 3, round: 1, round_pick: 3, team_name: "Another" }],
    ]);
    const history = renderBoard(board, "RB", { picked, mode: "drafted" });

    expect(history).not.toContain('class="trule"');
    expect(history.indexOf("Christian McCaffrey")).toBeLessThan(history.indexOf("Saquon Barkley"));
    expect(history.indexOf("Saquon Barkley")).toBeLessThan(history.indexOf("Bijan Robinson"));
  });

  it("keeps an unlisted persisted pick in the complete Drafted history", () => {
    const history = renderBoard(board, "ALL", {
      mode: "drafted",
      draftPicks: [
        {
          overall_pick: 17,
          round: 2,
          round_pick: 5,
          team_name: "Brian & Co",
          player_key: "manual:17",
          player_name: "Unlisted <Rookie>",
          player_pos: "RB",
          player_team: null,
        },
      ],
    });

    expect(history).toContain("Unlisted &lt;Rookie>");
    expect(history).toContain("2.05 · Brian &amp; Co");
  });
});

describe("renderBoard — windowed rendering", () => {
  it("renders only the top-ranked rows when a window limit is given", () => {
    const html = renderBoard(board, "ALL", { window: { limit: 2 } });

    expect(html.match(/class="rowA"/g)).toHaveLength(2);
    expect(html).toContain("Christian McCaffrey");
    expect(html).toContain("Ja'Marr Chase");
    expect(html).not.toContain("CeeDee Lamb");
  });

  it("appends a load-more sentinel counting the rows beyond the window", () => {
    const html = renderBoard(board, "ALL", { window: { limit: 2 } });

    expect(html).toContain('<button type="button" class="load-more" data-load-more data-remaining="14">Show 14 more players</button>');
    expect(html.endsWith("</button>")).toBe(true);
  });

  it("renders the full list with no sentinel when the window covers every row", () => {
    const windowed = renderBoard(board, "ALL", { window: { limit: board.players.length } });

    expect(windowed).toBe(renderBoard(board, "ALL"));
    expect(windowed).not.toContain("data-load-more");
  });

  it("keeps the sentinel distinct from selectable player rows", () => {
    const html = renderBoard(board, "ALL", { selectable: true, window: { limit: 2 } });
    const sentinel = html.slice(html.indexOf('class="load-more"'));

    expect(sentinel).not.toContain("data-player-key");
    expect(sentinel).not.toContain("rowA");
  });

  it("counts the whole remaining tier in dividers, not just the windowed rows", () => {
    const html = renderBoard(board, "RB", { window: { limit: 1 } });

    expect(html).toContain("Tier 1 · RB · 2 left");
    expect(html.match(/class="rowA"/g)).toHaveLength(1);
    expect(html).toContain('data-remaining="3"');
  });

  it("fills the window with available players only — picks don't consume slots", () => {
    const picked = new Map([["k0", { overall_pick: 1, round: 1, round_pick: 1, team_name: "Brian" }]]);
    const html = renderBoard(board, "ALL", { picked, window: { limit: 2 } });

    expect(html).not.toContain("Christian McCaffrey");
    expect(html).toContain("Ja'Marr Chase");
    expect(html).toContain("CeeDee Lamb");
    expect(html).toContain('data-remaining="13"');
  });

  it("windows search results and keeps the empty-search notice sentinel-free", () => {
    const results = board.players.slice(0, 3);
    const html = renderBoard(board, "ALL", { searchResults: results, window: { limit: 2 } });

    expect(html.match(/class="rowA"/g)).toHaveLength(2);
    expect(html).toContain('data-remaining="1"');

    const empty = renderBoard(board, "ALL", { searchResults: [], window: { limit: 2 } });
    expect(empty).toContain("No matching available players.");
    expect(empty).not.toContain("data-load-more");
  });

  it("windows the persisted drafted history, earliest picks first", () => {
    const draftPicks = board.players.slice(0, 3).map((player, i) => ({
      overall_pick: i + 1,
      round: 1,
      round_pick: i + 1,
      team_name: "Brian",
      player_key: player.key,
      player_name: player.name,
      player_pos: player.pos,
      player_team: player.team,
    }));
    const html = renderBoard(board, "ALL", { mode: "drafted", draftPicks, window: { limit: 2 } });

    expect(html).toContain("Christian McCaffrey");
    expect(html).toContain("Ja'Marr Chase");
    expect(html).not.toContain("CeeDee Lamb");
    expect(html).toContain('data-remaining="1"');
  });
});

describe("renderBoard — search replacement", () => {
  it("renders supplied results in relevance order without position filtering or tier grouping", () => {
    const results = [board.players.find((player) => player.key === "k2")!, board.players.find((player) => player.key === "k0")!];
    const html = renderBoard(board, "RB", { selectable: true, searchResults: results });

    expect(html.match(/<button type="button" class="rowA selectable"/g)).toHaveLength(2);
    expect(html.indexOf("Josh Allen")).toBeLessThan(html.indexOf("Christian McCaffrey"));
    expect(html).not.toContain('class="trule"');
    expect(html).not.toContain("Bijan Robinson");
    expect(html).toContain("--w:100%");
  });

  it("renders an explicit list-level state when search has no matches", () => {
    const html = renderBoard(board, "ALL", { selectable: true, searchResults: [] });

    expect(html).toContain('<div class="notice"><b>No matching available players.</b>');
    expect(html).toContain("No matching available players.");
    expect(html).not.toContain('class="rowA');
  });
});
