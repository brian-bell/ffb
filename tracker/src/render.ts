// Pure board renderer (slice-6 §3c). DOM-free: takes the v1 board + a position
// filter, returns an HTML string the client drops into the list mount. The
// tracker never re-ranks — the board is pre-sorted by VORP; this only groups and
// formats. Kept a pure function so it unit-tests without a DOM round-trip.

import type { Board, Player } from "./types";

export interface PickAnnotation {
  overall_pick: number;
  team_name: string;
}

export interface RenderOptions {
  picked?: ReadonlyMap<string, PickAnnotation>;
  mode?: "available" | "drafted";
}

// Positions that don't carry a meaningful positional rank suffix in the meta line.
const NO_POSRANK = new Set(["DEF", "K"]);

function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;");
}

function fmt1(v: number | null): string {
  return v == null ? "—" : v.toFixed(1);
}

function posLabel(p: Player): string {
  if (p.pos == null) return "—";
  return NO_POSRANK.has(p.pos) ? p.pos : `${p.pos}${p.pos_rank}`;
}

/** VORP micro-bar width (% of the board-wide max), 0 when unscored. */
function barWidth(vorp: number | null, maxVorp: number): number {
  if (vorp == null || maxVorp <= 0) return 0;
  return Math.round((vorp / maxVorp) * 100);
}

function vorpCell(p: Player, maxVorp: number): string {
  if (p.vorp == null) {
    return '<div class="num vorp na"><span class="tnum">—</span></div>';
  }
  const w = barWidth(p.vorp, maxVorp);
  return (
    `<div class="num vorp"><span class="bar" style="--w:${w}%"></span>` +
    `<span class="tnum">${p.vorp.toFixed(1)}</span></div>`
  );
}

/** +/- = adp_rank - rank, but only where both a value (VORP) and a market rank
 * exist; otherwise a neutral chip so ADP-only rows don't flash a false signal. */
function deltaChip(p: Player): string {
  const meaningful = p.vorp != null && p.adp_rank != null;
  if (!meaningful) return '<span class="chip na">—</span>';
  const d = p.adp_rank! - p.rank;
  const cls = d > 0 ? "up" : d < 0 ? "dn" : "zero";
  const txt = d > 0 ? `+${d}` : `${d}`;
  return `<span class="chip ${cls}">${txt}</span>`;
}

function metaLine(p: Player, showTier: boolean): string {
  const team = p.team ?? "FA";
  const bye = p.bye == null ? "—" : String(p.bye);
  let meta = `${posLabel(p)} · ${team} · BYE ${bye}`;
  if (showTier && p.tier != null) meta += ` · T${p.tier}`;
  return meta;
}

function row(p: Player, maxVorp: number, showTier: boolean, pick?: PickAnnotation): string {
  const adpNa = p.adp == null ? " na" : "";
  const annotation = pick
    ? `<span class="picknote">${pick.overall_pick}.${String(((pick.overall_pick - 1) % 100) + 1).padStart(2, "0")} · ${esc(pick.team_name)}</span>`
    : "";
  return (
    `<div class="rowA${pick ? " drafted" : ""}">` +
    annotation +
    `<span class="rk tnum">${p.rank}</span>` +
    `<span class="nm"><b>${esc(p.name)}</b><i>${esc(metaLine(p, showTier))}</i></span>` +
    vorpCell(p, maxVorp) +
    `<span class="num adp tnum${adpNa}">${fmt1(p.adp)}</span>` +
    deltaChip(p) +
    `</div>`
  );
}

function tierDivider(pos: string, tier: number | null): string {
  const label = tier == null ? "ADP only" : `Tier ${tier}`;
  return `<div class="trule">${label} · ${pos}</div>`;
}

/**
 * Render the board for a position filter.
 * - `"ALL"`: every player in board (rank) order, tier shown inline per row.
 * - a position: only that position, with sticky tier dividers between tiers.
 */
export function renderBoard(board: Board, filter: string, options: RenderOptions = {}): string {
  const players = board.players;
  const maxVorp = players.reduce((m, p) => (p.vorp != null && p.vorp > m ? p.vorp : m), 0);
  const picked = options.picked ?? new Map<string, PickAnnotation>();
  const history = options.mode === "drafted";

  const all = filter === "ALL";
  let rows = (all ? players : players.filter((p) => p.pos === filter)).filter((p) => (history ? picked.has(p.key) : !picked.has(p.key)));
  if (history) rows = [...rows].sort((a, b) => picked.get(a.key)!.overall_pick - picked.get(b.key)!.overall_pick);

  let html = "";
  let curTier: number | null | undefined = undefined;
  for (const p of rows) {
    if (!all && p.tier !== curTier) {
      curTier = p.tier;
      html += tierDivider(filter, p.tier);
    }
    html += row(p, maxVorp, all, picked.get(p.key));
  }
  return html;
}
