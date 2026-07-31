import { LIST_CHUNK } from "./board-view";

export interface MockViewState {
  position: string;
  visibleLimit: number;
}

export const initialMockView: MockViewState = {
  position: "ALL",
  visibleLimit: LIST_CHUNK,
};

export type MockViewEvent =
  | { type: "selectPosition"; position: string }
  | { type: "loadMore" };

export function nextMockView(state: MockViewState, event: MockViewEvent): MockViewState {
  switch (event.type) {
    case "selectPosition":
      return { position: event.position, visibleLimit: LIST_CHUNK };
    case "loadMore":
      return { ...state, visibleLimit: state.visibleLimit + LIST_CHUNK };
  }
}
