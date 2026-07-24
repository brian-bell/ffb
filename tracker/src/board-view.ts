import { BOARD_VERSION } from "./board";

export type BoardPosition = "ALL" | "QB" | "RB" | "WR" | "TE" | "K" | "DEF";
export type BoardMode = "available" | "drafted";

export interface BoardViewState {
  position: BoardPosition;
  mode: BoardMode;
  pickToolsExpanded: boolean;
  searchQuery: string;
  selectedKey: string | null;
  visibleLimit: number;
}

/** Rows rendered per progressive-loading step; the list starts at one chunk. */
export const LIST_CHUNK = 50;

export const initialBoardView: BoardViewState = {
  position: "ALL",
  mode: "available",
  pickToolsExpanded: false,
  searchQuery: "",
  selectedKey: null,
  visibleLimit: LIST_CHUNK,
};

export type BoardViewEvent =
  | { type: "selectPosition"; position: BoardPosition }
  | { type: "selectMode"; mode: BoardMode }
  | { type: "togglePickTools" }
  | { type: "searchChanged"; query: string }
  | { type: "playerSelected"; key: string }
  | { type: "selectionCleared" }
  | { type: "pickRecorded" }
  | { type: "loadMore" };

export function nextBoardView(state: BoardViewState, event: BoardViewEvent): BoardViewState {
  switch (event.type) {
    case "selectPosition":
      return { ...state, position: event.position, visibleLimit: LIST_CHUNK };
    case "selectMode":
      return { ...state, mode: event.mode, visibleLimit: LIST_CHUNK };
    case "togglePickTools":
      return { ...state, pickToolsExpanded: !state.pickToolsExpanded };
    case "searchChanged":
      return { ...state, searchQuery: event.query, visibleLimit: LIST_CHUNK };
    case "playerSelected":
      return {
        ...state,
        selectedKey: state.selectedKey === event.key ? null : event.key,
        pickToolsExpanded: true,
      };
    case "selectionCleared":
      return { ...state, selectedKey: null };
    case "pickRecorded":
      return { ...state, pickToolsExpanded: false, searchQuery: "", selectedKey: null, visibleLimit: LIST_CHUNK };
    case "loadMore":
      return { ...state, visibleLimit: state.visibleLimit + LIST_CHUNK };
  }
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
