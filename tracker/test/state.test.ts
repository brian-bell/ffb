import { describe, it, expect } from "vitest";
import { nextState, makeStore, initialState, type UiState } from "../src/state";

describe("nextState — boot", () => {
  it("no stored key → first-run gate, locked", () => {
    expect(nextState(initialState, { type: "boot", hasKey: false })).toEqual({
      locked: true,
      modal: "first",
      error: null,
    });
  });

  it("stored key → unlocked, no modal", () => {
    expect(nextState(initialState, { type: "boot", hasKey: true })).toEqual({
      locked: false,
      modal: "hidden",
      error: null,
    });
  });
});

describe("nextState — key submission", () => {
  const gate: UiState = { locked: true, modal: "first", error: null };

  it("200 → unlocked, modal closed, error cleared", () => {
    expect(nextState(gate, { type: "unlock" })).toEqual({
      locked: false,
      modal: "hidden",
      error: null,
    });
  });

  it("401 → cleared back to the first-run gate with an error", () => {
    const s = nextState({ locked: false, modal: "settings", error: null }, { type: "invalid" });
    expect(s.locked).toBe(true);
    expect(s.modal).toBe("first");
    expect(s.error).toMatch(/invalid/i);
  });

  it("empty input → keeps the current modal open with an error", () => {
    const s = nextState({ locked: false, modal: "settings", error: null }, { type: "empty" });
    expect(s.modal).toBe("settings");
    expect(s.locked).toBe(false);
    expect(s.error).toMatch(/enter/i);
  });

  it("network error → keeps the modal, shows a retry notice", () => {
    const s = nextState(gate, { type: "network" });
    expect(s.modal).toBe("first");
    expect(s.error).toMatch(/network|retry/i);
  });
});

describe("nextState — returning-device boot network failure", () => {
  it("opens the first-run modal with the network error preserved (atomic)", () => {
    // A saved-key device whose initial /api/board fetch fails: must land on the
    // modal WITH the network notice, not a blank error (the openModal-clears-error
    // sequencing bug this event replaces).
    const s = nextState(initialState, { type: "bootNetwork" });
    expect(s.modal).toBe("first");
    expect(s.error).toMatch(/network|retry/i);
  });
});

describe("nextState — modal controls", () => {
  it("gear opens settings when a key exists", () => {
    const s = nextState({ locked: false, modal: "hidden", error: null }, { type: "openModal", mode: "settings" });
    expect(s.modal).toBe("settings");
    expect(s.error).toBeNull();
  });

  it("close hides the modal", () => {
    expect(nextState({ locked: false, modal: "settings", error: "x" }, { type: "closeModal" })).toEqual({
      locked: false,
      modal: "hidden",
      error: null,
    });
  });

  it("forget → cleared, locked, back to the first-run gate", () => {
    expect(nextState({ locked: false, modal: "settings", error: null }, { type: "forget" })).toEqual({
      locked: true,
      modal: "first",
      error: null,
    });
  });
});

describe("makeStore — localStorage with in-memory fallback", () => {
  it("uses the provided storage when it works", () => {
    const backing = new Map<string, string>();
    const store = makeStore({
      getItem: (k) => backing.get(k) ?? null,
      setItem: (k, v) => void backing.set(k, v),
      removeItem: (k) => void backing.delete(k),
    });
    store.set("abc");
    expect(store.get()).toBe("abc");
    store.del();
    expect(store.get()).toBeNull();
  });

  it("falls back to memory when storage throws (private mode / sandboxed frame)", () => {
    const throwing = {
      getItem: () => {
        throw new Error("denied");
      },
      setItem: () => {
        throw new Error("denied");
      },
      removeItem: () => {
        throw new Error("denied");
      },
    };
    const store = makeStore(throwing);
    expect(store.get()).toBeNull();
    store.set("k"); // must not throw
    expect(store.get()).toBe("k"); // served from memory
    store.del();
    expect(store.get()).toBeNull();
  });

  it("degrades to memory when there is no storage at all", () => {
    const store = makeStore(null);
    store.set("z");
    expect(store.get()).toBe("z");
  });

  it("keeps the key when writes fail but reads succeed (Safari private mode)", () => {
    // The partial-failure mode: setItem throws (quota / private browsing) while
    // getItem still works and returns null. The all-throwing case is not enough —
    // get() must fall back to the memory copy set() stashed.
    const store = makeStore({
      getItem: () => null,
      setItem: () => {
        throw new Error("QuotaExceededError");
      },
      removeItem: () => {},
    });
    store.set("k");
    expect(store.get()).toBe("k");
    store.del();
    expect(store.get()).toBeNull();
  });
});
