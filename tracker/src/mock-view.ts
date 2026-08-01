import { initialBoardView, type BoardViewState } from "./board-view";

export function reconcileMockBoardView(
  state: BoardViewState,
  previousMockId: string | null,
  nextMockId: string | null,
  availablePlayers: readonly { key: string }[],
): BoardViewState {
  if (previousMockId !== nextMockId) return initialBoardView;
  if (
    state.selectedKey !== null
    && !availablePlayers.some((player) => player.key === state.selectedKey)
  ) return { ...state, selectedKey: null };
  return state;
}
