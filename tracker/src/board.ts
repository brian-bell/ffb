// KV board access + version pin (slice-6 §3a/§3d). The Worker streams the stored
// text straight through without parsing — the Python pipeline owns the contract
// shape. `validateVersion` is the client-side pin that turns contract drift into
// a loud "redeploy the tracker" notice instead of a silent mis-render.

import type { Board } from "./types";

export const BOARD_KEY = "board:current";
export const BOARD_VERSION = 1;

export interface BoardEnv {
  BOARD: KVNamespace;
}

/** Raw board JSON text from KV, or null when nothing has been published yet. */
export async function getBoardText(env: BoardEnv): Promise<string | null> {
  return env.BOARD.get(BOARD_KEY);
}

/** True only when the board carries exactly the version this tracker renders. */
export function validateVersion(board: unknown): boolean {
  return Boolean(board && typeof board === "object" && !Array.isArray(board) && (board as { version?: unknown }).version === BOARD_VERSION);
}

function finite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function nullableFinite(value: unknown): boolean {
  return value === null || finite(value);
}

function nullableString(value: unknown): boolean {
  return value === null || typeof value === "string";
}

/** Strict v1 input guard for the fields the client and pick API consume. */
export function isValidBoard(value: unknown): value is Board {
  if (!value || typeof value !== "object") return false;
  const board = value as Record<string, unknown>;
  if (!validateVersion(board) || !finite(board.season) || typeof board.generated_at !== "string" || typeof board.scoring !== "string" || !finite(board.num_teams) || !Array.isArray(board.players) || !board.roster_slots || typeof board.roster_slots !== "object" || Array.isArray(board.roster_slots)) return false;
  if (!Object.values(board.roster_slots as Record<string, unknown>).every((count) => Number.isInteger(count) && (count as number) >= 0)) return false;
  return board.players.every((raw) => {
    if (!raw || typeof raw !== "object") return false;
    const player = raw as Record<string, unknown>;
    return typeof player.key === "string" && typeof player.name === "string" && nullableString(player.pos) && nullableString(player.team) && nullableFinite(player.bye) && nullableFinite(player.points) && finite(player.n_sources) && nullableFinite(player.vorp) && nullableFinite(player.tier) && finite(player.rank) && finite(player.pos_rank) && nullableFinite(player.adp) && nullableFinite(player.adp_rank) && nullableFinite(player.adp_high) && nullableFinite(player.adp_low) && nullableFinite(player.adp_stdev) && typeof player.matched === "boolean";
  });
}
