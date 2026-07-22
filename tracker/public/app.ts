// Client boot: reads the key from the store, gates the board behind the key
// modal, fetches /api/board, and renders by position → tier. The testable core
// (the reducer, the store, the renderer) lives in ../src; this file is the thin
// DOM wiring around them (slice-6 §3c/§6).

import { renderBoard } from "../src/render";
import { validateVersion, BOARD_VERSION } from "../src/board";
import { makeStore, nextState, initialState, type UiState, type UiEvent } from "../src/state";
import type { Board } from "../src/types";

const POSITIONS = ["ALL", "QB", "RB", "WR", "TE", "K", "DEF"];

function localStorageOrNull(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

const store = makeStore(localStorageOrNull());

// ---- DOM refs ----
const $ = <T extends Element>(sel: string): T => {
  const el = document.querySelector<T>(sel);
  if (!el) throw new Error(`missing element: ${sel}`);
  return el;
};

const screenEl = $<HTMLElement>("[data-screen]");
const lockEl = $<HTMLElement>("[data-lock]");
const titleEl = $<HTMLElement>("[data-title]");
const descEl = $<HTMLElement>("[data-desc]");
const inputEl = $<HTMLInputElement>("[data-input]");
const errEl = $<HTMLElement>("[data-err]");
const saveEl = $<HTMLButtonElement>("[data-save]");
const forgetEl = $<HTMLButtonElement>("[data-forget]");
const closeEl = $<HTMLButtonElement>("[data-close]");
const gearEl = $<HTMLButtonElement>("[data-gear]");
const tabsEl = $<HTMLElement>("[data-tabs]");
const listEl = $<HTMLElement>("[data-list]");
const footEl = $<HTMLElement>("[data-foot]");
const subEl = $<HTMLElement>("[data-sub]");

// ---- app state ----
let ui: UiState = initialState;
let board: Board | null = null;
let active = "ALL";

function dispatch(event: UiEvent): void {
  ui = nextState(ui, event);
  applyUi();
}

function applyUi(): void {
  screenEl.classList.toggle("locked", ui.locked);
  const settings = ui.modal === "settings";
  if (ui.modal === "hidden") {
    lockEl.hidden = true;
  } else {
    lockEl.hidden = false;
    titleEl.textContent = settings ? "Board settings" : "Unlock your board";
    descEl.textContent = settings
      ? "Your board is unlocked on this device. Update the key, or forget it to start over."
      : "Enter your tracker API key to load the draft board on this device.";
    saveEl.textContent = settings ? "Update key" : "Unlock board";
    forgetEl.hidden = !settings;
    closeEl.hidden = !settings; // the first-run gate can't be dismissed without a key
  }
  errEl.hidden = ui.error == null;
  errEl.textContent = ui.error ?? "";
  if (ui.modal !== "hidden") {
    inputEl.value = "";
    setTimeout(() => inputEl.focus(), 50);
  }
}

// ---- board rendering ----
function renderList(): void {
  if (!board) {
    listEl.innerHTML =
      '<div class="notice"><b>No board published yet.</b>' +
      "Run <code>ffb cheatsheet --export</code> then <code>npm run publish:board</code>.</div>";
    return;
  }
  listEl.innerHTML = renderBoard(board, active);
  listEl.scrollTop = 0;
}

function renderChrome(): void {
  if (!board) return;
  subEl.textContent = `${board.num_teams}-team · ${board.scoring} scoring`;
  let when = board.generated_at;
  try {
    when = new Date(board.generated_at).toLocaleString();
  } catch {
    /* keep raw */
  }
  footEl.textContent = `board.json v${board.version} · as of ${when}`;
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function showContractDrift(got: unknown): void {
  board = null;
  // `got` is the board's own version field, echoed back from a possibly
  // malformed/tampered KV value — escape it before it touches innerHTML.
  const version = escapeHtml(String(got));
  listEl.innerHTML =
    '<div class="notice"><b>Board format changed.</b>' +
    `This tracker renders v${BOARD_VERSION}, but the board is v${version}. ` +
    "Redeploy the tracker (<code>npm run deploy</code>) to match the new contract.</div>";
}

function renderTabs(): void {
  tabsEl.innerHTML = POSITIONS.map(
    (p) => `<button class="tab" role="tab" aria-selected="${p === active}" data-pos="${p}">${p}</button>`,
  ).join("");
}

// ---- data fetch ----
type LoadResult = "ok" | "unauthorized" | "empty" | "network" | "drift";

async function loadBoard(key: string): Promise<LoadResult> {
  let res: Response;
  try {
    res = await fetch("/api/board", { headers: { Authorization: `Bearer ${key}` } });
  } catch {
    return "network";
  }
  if (res.status === 401) return "unauthorized";
  if (res.status === 404) return "empty";
  if (!res.ok) return "network";

  let parsed: Board;
  try {
    parsed = (await res.json()) as Board;
  } catch {
    return "network";
  }
  if (!validateVersion(parsed)) {
    showContractDrift((parsed as { version?: unknown }).version);
    return "drift";
  }
  board = parsed;
  renderChrome();
  renderList();
  return "ok";
}

// ---- key submission ----
async function submitKey(): Promise<void> {
  const key = inputEl.value.trim();
  if (!key) {
    dispatch({ type: "empty" });
    return;
  }
  const result = await loadBoard(key);
  switch (result) {
    case "ok":
    case "drift":
    case "empty":
      // The key authenticated (even a 404/drift means the gate passed); persist + unlock.
      store.set(key);
      dispatch({ type: "unlock" });
      if (result === "empty") {
        board = null;
        renderList();
      }
      break;
    case "unauthorized":
      store.del();
      dispatch({ type: "invalid" });
      break;
    case "network":
      dispatch({ type: "network" });
      break;
  }
}

// ---- events ----
saveEl.addEventListener("click", () => void submitKey());
inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter") void submitKey();
});
forgetEl.addEventListener("click", () => {
  store.del();
  board = null;
  renderList();
  dispatch({ type: "forget" });
});
closeEl.addEventListener("click", () => {
  if (store.get()) dispatch({ type: "closeModal" });
});
lockEl.addEventListener("click", (e) => {
  if (e.target === lockEl && store.get()) dispatch({ type: "closeModal" });
});
gearEl.addEventListener("click", () => {
  dispatch({ type: "openModal", mode: store.get() ? "settings" : "first" });
});
tabsEl.addEventListener("click", (e) => {
  const btn = (e.target as HTMLElement).closest<HTMLButtonElement>(".tab");
  if (!btn) return;
  active = btn.dataset.pos ?? "ALL";
  tabsEl.querySelectorAll<HTMLButtonElement>(".tab").forEach((t) => {
    t.setAttribute("aria-selected", String(t.dataset.pos === active));
  });
  renderList();
});

// ---- first run vs. returning device ----
async function boot(): Promise<void> {
  renderTabs();
  const key = store.get();
  if (!key) {
    dispatch({ type: "boot", hasKey: false });
    renderList();
    return;
  }
  const result = await loadBoard(key);
  if (result === "unauthorized") {
    store.del();
    dispatch({ type: "invalid" });
  } else if (result === "network") {
    // Returning device, transient fetch failure: open the first-run modal with
    // the network error preserved (one atomic transition — see bootNetwork).
    dispatch({ type: "bootNetwork" });
  } else {
    dispatch({ type: "boot", hasKey: true });
    if (result === "empty") {
      // Valid key, but nothing published (or it was removed) — show the
      // publish instruction instead of a blank board.
      board = null;
      renderList();
    }
  }
}

void boot();
