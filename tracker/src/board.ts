// KV board access + version pin (slice-6 §3a/§3d). The Worker streams the stored
// text straight through without parsing — the Python pipeline owns the contract
// shape. `validateVersion` is the client-side pin that turns contract drift into
// a loud "redeploy the tracker" notice instead of a silent mis-render.

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
export function validateVersion(board: { version?: unknown }): boolean {
  return board.version === BOARD_VERSION;
}
