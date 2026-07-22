import { BOARD_VERSION } from "./board";

function esc(value: unknown): string {
  return String(value).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/** User-facing board fallback, retaining the specific action for contract drift. */
export function boardNoticeHtml(driftVersion: unknown | null): string {
  if (driftVersion !== null) {
    return (
      '<div class="notice"><b>Board format changed.</b>' +
      `This tracker renders v${BOARD_VERSION}, but the board is v${esc(driftVersion)}. ` +
      "Redeploy the tracker (<code>npm run deploy</code>) to match the new contract.</div>"
    );
  }
  return (
    '<div class="notice"><b>No board published yet.</b>' +
    "Run <code>ffb cheatsheet --export</code> then <code>npm run publish:board</code>.</div>"
  );
}
