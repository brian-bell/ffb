// Frontend boot logic, extracted pure so it unit-tests without a DOM (slice-6
// §6 test 5). `nextState` is the key/lock/modal reducer; `makeStore` wraps
// localStorage with an in-memory fallback so the gate still functions in private
// mode or a sandboxed frame where storage throws.

export type ModalMode = "hidden" | "first" | "settings";

export interface UiState {
  locked: boolean;
  modal: ModalMode;
  error: string | null;
}

export type UiEvent =
  | { type: "boot"; hasKey: boolean }
  | { type: "unlock" } // 200 from /api/board
  | { type: "invalid" } // 401 — key rejected/cleared
  | { type: "empty" } // blank submit
  | { type: "network" } // fetch failed
  | { type: "openModal"; mode: "first" | "settings" }
  | { type: "closeModal" }
  | { type: "forget" };

export const initialState: UiState = { locked: true, modal: "hidden", error: null };

const ERR_INVALID = "Invalid API key. Check it and try again.";
const ERR_EMPTY = "Enter your API key to continue.";
const ERR_NETWORK = "Network error — check your connection and retry.";

const gate: UiState = { locked: true, modal: "first", error: null };

export function nextState(state: UiState, event: UiEvent): UiState {
  switch (event.type) {
    case "boot":
      return event.hasKey ? { locked: false, modal: "hidden", error: null } : gate;
    case "unlock":
      return { locked: false, modal: "hidden", error: null };
    case "invalid":
      // The stored key is cleared by the caller; drop back to the mandatory gate.
      return { locked: true, modal: "first", error: ERR_INVALID };
    case "empty":
      return { ...state, error: ERR_EMPTY };
    case "network":
      return { ...state, error: ERR_NETWORK };
    case "openModal":
      return { ...state, modal: event.mode, error: null };
    case "closeModal":
      return { ...state, modal: "hidden", error: null };
    case "forget":
      return gate;
  }
}

export interface Store {
  get(): string | null;
  set(v: string): void;
  del(): void;
}

interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

const STORAGE_KEY = "ffb.trackerKey";

/** localStorage-backed key store that transparently degrades to memory. */
export function makeStore(storage: StorageLike | null): Store {
  let mem: string | null = null;
  return {
    get() {
      // `mem` is a write-through fallback: prefer a stored value, but fall back
      // to memory when storage yields nothing (a failed/blocked write) or throws.
      try {
        const stored = storage ? storage.getItem(STORAGE_KEY) : null;
        return stored ?? mem;
      } catch {
        return mem;
      }
    },
    set(v: string) {
      mem = v;
      try {
        storage?.setItem(STORAGE_KEY, v);
      } catch {
        /* memory already holds it */
      }
    },
    del() {
      mem = null;
      try {
        storage?.removeItem(STORAGE_KEY);
      } catch {
        /* ignore */
      }
    },
  };
}
