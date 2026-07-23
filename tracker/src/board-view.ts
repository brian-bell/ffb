import { BOARD_VERSION } from "./board";

export type BoardPosition = "ALL" | "QB" | "RB" | "WR" | "TE" | "K" | "DEF";
export type BoardMode = "available" | "drafted";

export interface BoardViewState {
  position: BoardPosition;
  mode: BoardMode;
  pickToolsExpanded: boolean;
}

export const initialBoardView: BoardViewState = {
  position: "ALL",
  mode: "available",
  pickToolsExpanded: false,
};

export type BoardViewEvent =
  | { type: "selectPosition"; position: BoardPosition }
  | { type: "selectMode"; mode: BoardMode }
  | { type: "togglePickTools" }
  | { type: "pickRecorded" };

export function nextBoardView(state: BoardViewState, event: BoardViewEvent): BoardViewState {
  switch (event.type) {
    case "selectPosition":
      return { ...state, position: event.position };
    case "selectMode":
      return { ...state, mode: event.mode };
    case "togglePickTools":
      return { ...state, pickToolsExpanded: !state.pickToolsExpanded };
    case "pickRecorded":
      return { ...state, pickToolsExpanded: false };
  }
}

export interface BoardViewDescription {
  lead: string;
  detail: string;
  orderLabel: string;
}

export function describeBoardView(state: BoardViewState): BoardViewDescription {
  if (state.mode === "available" && state.position === "ALL") {
    return {
      lead: "Overall board",
      detail: "Tier badges stay inline. Choose a position to group by tier.",
      orderLabel: "BOARD ORDER",
    };
  }
  if (state.mode === "available") {
    return {
      lead: `${state.position} tiers`,
      detail: `Available ${state.position}s are grouped by positional tier.`,
      orderLabel: "TIER ORDER",
    };
  }
  if (state.position === "ALL") {
    return {
      lead: "Draft log",
      detail: "All recorded picks are in chronological order; tiers stay inline.",
      orderLabel: "PICK ORDER",
    };
  }
  return {
    lead: `${state.position} draft log`,
    detail: `Filtered ${state.position} history stays chronological; no tier regrouping.`,
    orderLabel: "PICK ORDER",
  };
}

function esc(value: unknown): string {
  return String(value).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/** User-facing board fallback, retaining the specific action for contract drift. */
export function boardNoticeHtml(driftVersion: unknown | null, malformed = false): string {
  if (driftVersion !== null) {
    return (
      '<div class="notice"><b>Board format changed.</b>' +
      `This tracker renders v${BOARD_VERSION}, but the board is v${esc(driftVersion)}. ` +
      "Redeploy the tracker (<code>npm run deploy</code>) to match the new contract.</div>"
    );
  }
  if (malformed) {
    return '<div class="notice"><b>Board data is incomplete.</b>Republish the board, then reload this page.</div>';
  }
  return (
    '<div class="notice"><b>No board published yet.</b>' +
    "Run <code>ffb season sync</code> and <code>ffb board export</code>, then " +
      "<code>npm run publish:board</code>.</div>"
  );
}
