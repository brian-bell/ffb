// Pure board renderer (slice-6 §3c). DOM-free: takes the v1 board + a position
// filter, returns an HTML string the client drops into the list mount. The
// tracker never re-ranks — the board is pre-sorted by VORP; this only groups and
// formats. Kept a pure function so it unit-tests without a DOM round-trip.

import type { Board, Player } from "./types";
import { normalizedPosition, playersEquivalent } from "./player-identity";

export interface PickAnnotation {
  overall_pick: number;
  round: number;
  round_pick: number;
  team_name: string;
}

export interface DraftPickSnapshot extends PickAnnotation {
  player_key: string;
  player_name: string;
  player_pos: string | null;
  player_team: string | null;
}

export interface RenderOptions {
  picked?: ReadonlyMap<string, PickAnnotation>;
  mode?: "available" | "drafted";
  draftPicks?: readonly DraftPickSnapshot[];
  selectable?: boolean;
  selectedKey?: string | null;
  searchResults?: readonly Player[];
  /** Progressive loading: render only the first `limit` rows. */
  window?: { limit: number };
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
  return NO_POSRANK.has(p.pos) || p.pos_rank <= 0 ? p.pos : `${p.pos}${p.pos_rank}`;
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

function tierChip(p: Player): string {
  const label = p.tier == null ? "ADP" : `T${p.tier}`;
  return `<span class="tier-chip">${esc(label)}</span>`;
}

function tierKey(tier: number | null): string {
  return tier == null ? "adp" : String(tier);
}

function metaLine(p: Player): string {
  const team = p.team ?? "FA";
  const bye = p.bye == null ? "—" : String(p.bye);
  return `${posLabel(p)} · ${team} · BYE ${bye}`;
}

function row(p: Player, maxVorp: number, pick?: PickAnnotation, selectable = false, selectedKey?: string | null): string {
  const adpNa = p.adp == null ? " na" : "";
  const selected = selectable && p.key === selectedKey;
  const annotation = pick
    ? `<span class="picknote">${pick.round}.${String(pick.round_pick).padStart(2, "0")} · ${esc(pick.team_name)}</span>`
    : "";
  const open = selectable
    ? `<button type="button" class="rowA selectable${selected ? " selected" : ""}" data-player-key="${encodeURIComponent(p.key)}" aria-pressed="${selected}">`
    : `<div class="rowA${pick ? " drafted" : ""}">`;
  return (
    open +
    annotation +
    `<span class="rk tnum">${p.rank}</span>` +
    `<span class="nm"><b>${esc(p.name)}</b><i>${tierChip(p)}${esc(metaLine(p))}</i></span>` +
    vorpCell(p, maxVorp) +
    `<span class="num adp tnum${adpNa}">${fmt1(p.adp)}</span>` +
    deltaChip(p) +
    (selectable ? `</button>` : `</div>`)
  );
}

/** "Show more" sentinel for progressive loading — the client observes/clicks it. */
function loadMoreSentinel(remaining: number): string {
  return (
    `<button type="button" class="load-more" data-load-more data-remaining="${remaining}">` +
    `Show ${remaining} more players</button>`
  );
}

function tierDivider(pos: string, tier: number | null, count: number): string {
  const label = tier == null ? "ADP only" : `Tier ${tier}`;
  return `<div class="trule" data-tier-key="${tierKey(tier)}" data-tier-count="${count}">${label} · ${esc(pos)} · ${count} left</div>`;
}

function playerFromPick(players: readonly Player[], pick: DraftPickSnapshot): Player {
  const identity = {
    key: pick.player_key,
    name: pick.player_name,
    pos: pick.player_pos,
    team: pick.player_team,
  };
  const boardPlayer = players.find((player) => playersEquivalent(player, identity));
  const displayPosition = normalizedPosition(pick.player_pos) ?? pick.player_pos;
  return {
    key: pick.player_key,
    name: pick.player_name,
    pos: displayPosition,
    team: pick.player_team,
    bye: boardPlayer?.bye ?? null,
    points: boardPlayer?.points ?? null,
    n_sources: boardPlayer?.n_sources ?? 0,
    vorp: boardPlayer?.vorp ?? null,
    tier: boardPlayer?.tier ?? null,
    rank: boardPlayer?.rank ?? pick.overall_pick,
    pos_rank: boardPlayer?.pos_rank ?? 0,
    adp: boardPlayer?.adp ?? null,
    adp_rank: boardPlayer?.adp_rank ?? null,
    adp_high: boardPlayer?.adp_high ?? null,
    adp_low: boardPlayer?.adp_low ?? null,
    adp_stdev: boardPlayer?.adp_stdev ?? null,
    matched: boardPlayer?.matched ?? false,
  };
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
  const searching = options.searchResults !== undefined;
  const history = !searching && options.mode === "drafted";

  if (history && options.draftPicks) {
    return [...options.draftPicks]
      .filter((pick) => filter === "ALL" || normalizedPosition(pick.player_pos) === filter)
      .sort((a, b) => a.overall_pick - b.overall_pick)
      .map((pick) => row(playerFromPick(players, pick), maxVorp, pick))
      .join("");
  }

  const all = filter === "ALL";
  const grouped = !searching && !history && !all;
  const visiblePlayers = searching ? options.searchResults! : all ? players : players.filter((p) => p.pos === filter);
  let rows = visiblePlayers.filter((p) => (history ? picked.has(p.key) : !picked.has(p.key)));
  if (searching && rows.length === 0) {
    return '<div class="notice"><b>No matching available players.</b>Try another name, team, or position.</div>';
  }
  if (history) rows = [...rows].sort((a, b) => picked.get(a.key)!.overall_pick - picked.get(b.key)!.overall_pick);
  const tierCounts = new Map<number | null, number>();
  for (const player of rows) tierCounts.set(player.tier, (tierCounts.get(player.tier) ?? 0) + 1);

  const limit = options.window?.limit ?? rows.length;
  let html = "";
  let curTier: number | null | undefined = undefined;
  for (const p of rows.slice(0, limit)) {
    if (grouped && p.tier !== curTier) {
      curTier = p.tier;
      html += tierDivider(filter, p.tier, tierCounts.get(p.tier) ?? 0);
    }
    html += row(p, maxVorp, picked.get(p.key), options.selectable === true && !history, options.selectedKey);
  }
  if (rows.length > limit) html += loadMoreSentinel(rows.length - limit);
  return html;
}
