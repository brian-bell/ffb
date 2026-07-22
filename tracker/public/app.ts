// Client boot: reads the key from the store, gates the board behind the key
// modal, fetches /api/board, and renders by position → tier. The testable core
// (the reducer, the store, the renderer) lives in ../src; this file is the thin
// DOM wiring around them (slice-6 §3c/§6).

import { renderBoard } from "../src/render";
import { validateVersion, BOARD_VERSION } from "../src/board";
import { makeStore, nextState, resetsKeyInput, initialState, type UiState, type UiEvent } from "../src/state";
import { searchPlayers, suggestPlayers } from "../src/suggestions";
import type { DraftState } from "../src/draft-store";
import type { Board, Player } from "../src/types";

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
const clockEl = $<HTMLElement>("[data-clock]");
const viewTabsEl = $<HTMLElement>("[data-view-tabs]");
const recentEl = $<HTMLElement>("[data-recent]");
const setupEl = $<HTMLElement>("[data-setup]");
const draftNameEl = $<HTMLInputElement>("[data-draft-name]");
const roundsEl = $<HTMLInputElement>("[data-rounds]");
const teamNamesEl = $<HTMLTextAreaElement>("[data-team-names]");
const userTeamEl = $<HTMLElement>("[data-user-team]") as unknown as HTMLSelectElement;
const saveDraftEl = $<HTMLButtonElement>("[data-save-draft]");
const pickPanelEl = $<HTMLElement>("[data-pick-panel]");
const onClockEl = $<HTMLElement>("[data-on-clock]");
const suggestionsEl = $<HTMLElement>("[data-suggestions]");
const playerSearchEl = $<HTMLInputElement>("[data-player-search]");
const searchResultsEl = $<HTMLElement>("[data-search-results]");
const selectedEl = $<HTMLElement>("[data-selected]");
const draftErrorEl = $<HTMLElement>("[data-draft-error]");
const recordPickEl = $<HTMLButtonElement>("[data-record-pick]");
const undoPickEl = $<HTMLButtonElement>("[data-undo-pick]");
const resetDraftEl = $<HTMLButtonElement>("[data-reset-draft]");

// ---- app state ----
let ui: UiState = initialState;
let board: Board | null = null;
let active = "ALL";
let view: "available" | "drafted" = "available";
let draft: DraftState | null = null;
let selected: Player | null = null;
let writing = false;

function dispatch(event: UiEvent): void {
  ui = nextState(ui, event);
  applyUi();
  // Clear + refocus the field only when the modal is freshly presented — never
  // on a retryable failure, which would wipe the key the user just typed.
  if (ui.modal !== "hidden" && resetsKeyInput(event)) {
    inputEl.value = "";
    setTimeout(() => inputEl.focus(), 50);
  }
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
}

// ---- board rendering ----
function renderList(): void {
  if (!board) {
    listEl.innerHTML =
      '<div class="notice"><b>No board published yet.</b>' +
      "Run <code>ffb cheatsheet --export</code> then <code>npm run publish:board</code>.</div>";
    return;
  }
  const picked = new Map((draft?.picks ?? []).map((pick) => [pick.player_key, pick]));
  listEl.innerHTML = renderBoard(board, active, { picked, mode: view });
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

function keyHeader(): HeadersInit {
  return { Authorization: `Bearer ${store.get() ?? ""}` };
}

function playerByKey(key: string): Player | null {
  return board?.players.find((player) => player.key === key) ?? null;
}

function setSelected(player: Player | null): void {
  selected = player;
  selectedEl.innerHTML = player ? `Selected: <b>${escapeHtml(player.name)}</b> · ${escapeHtml(player.pos ?? "—")} · ${escapeHtml(player.team ?? "FA")}` : "No player selected.";
  recordPickEl.disabled = !player || !draft?.next || writing;
}

function setDraftError(message = ""): void {
  draftErrorEl.textContent = message;
}

function refreshUserTeams(): void {
  const selectedName = userTeamEl.value;
  const names = teamNamesEl.value.split("\n").map((name) => name.trim()).filter(Boolean);
  userTeamEl.innerHTML = names.map((name) => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`).join("");
  if (names.includes(selectedName)) userTeamEl.value = selectedName;
}

function renderDraft(): void {
  const configured = draft?.configured === true;
  setupEl.hidden = configured;
  pickPanelEl.hidden = !configured;
  recentEl.hidden = !configured;
  if (!configured) {
    clockEl.textContent = "Set up draft";
    subEl.textContent = board ? `${board.num_teams}-team · ${board.scoring} scoring` : "—";
    renderList();
    return;
  }
  const next = draft!.next;
  if (!next) {
    clockEl.textContent = "Draft complete";
    onClockEl.textContent = "Draft complete — undo the latest pick to reopen it.";
  } else {
    const pick = `${next.round}.${String(next.round_pick).padStart(2, "0")}`;
    clockEl.textContent = `${pick} · ${next.is_user ? "YOU · " : ""}${next.team_name} ${next.direction === "forward" ? "↓" : "↑"}`;
    onClockEl.textContent = `Round ${next.round} of ${draft!.draft!.rounds} · Pick ${next.overall_pick} of ${draft!.draft!.team_count * draft!.draft!.rounds} · ${next.team_name} is on the clock`;
  }
  const pickedKeys = new Set(draft!.picks.map((pick) => pick.player_key));
  suggestionsEl.innerHTML = !next || !board ? "" : suggestPlayers(board.players, pickedKeys)
    .map((player) => `<button class="suggestion" data-player-key="${encodeURIComponent(player.key)}"><b>${escapeHtml(player.name)}</b><small>${escapeHtml(player.pos ?? "—")} · ${escapeHtml(player.team ?? "FA")} · ADP ${player.adp ?? "—"}</small></button>`)
    .join("");
  recentEl.innerHTML = draft!.picks.length
    ? [...draft!.picks].reverse().slice(0, 6).map((pick) => `<div><b>${pick.round}.${String(pick.round_pick).padStart(2, "0")}</b> · ${escapeHtml(pick.player_name)} — ${escapeHtml(pick.team_name)}</div>`).join("")
    : "No picks recorded.";
  const canPick = Boolean(next && board);
  playerSearchEl.disabled = !canPick || writing;
  undoPickEl.disabled = writing || draft!.picks.length === 0;
  undoPickEl.textContent = draft!.picks.length ? `Undo ${draft!.picks.at(-1)!.round}.${String(draft!.picks.at(-1)!.round_pick).padStart(2, "0")} — ${draft!.picks.at(-1)!.player_name}` : "Undo latest pick";
  setSelected(canPick ? selected : null);
  renderList();
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

async function loadDraft(): Promise<LoadResult> {
  let res: Response;
  try {
    res = await fetch("/api/draft", { headers: keyHeader() });
  } catch {
    return "network";
  }
  if (res.status === 401) return "unauthorized";
  if (!res.ok) return "network";
  try {
    draft = (await res.json()) as DraftState;
    renderDraft();
    return "ok";
  } catch {
    return "network";
  }
}

async function writeDraft(path: string, method: string, payload?: unknown): Promise<boolean> {
  writing = true;
  renderDraft();
  try {
    const res = await fetch(path, {
      method,
      headers: { ...keyHeader(), "content-type": "application/json" },
      body: payload === undefined ? undefined : JSON.stringify(payload),
    });
    if (res.status === 401) {
      store.del();
      dispatch({ type: "invalid" });
      return false;
    }
    const response = (await res.json()) as DraftState & { error?: string; message?: string };
    if (!res.ok) {
      setDraftError(response.message ?? response.error ?? "Could not update draft.");
      if (res.status === 409) await loadDraft();
      return false;
    }
    draft = response;
    selected = null;
    playerSearchEl.value = "";
    searchResultsEl.innerHTML = "";
    setDraftError("");
    renderDraft();
    return true;
  } catch {
    setDraftError("Network error — draft state was not changed here.");
    return false;
  } finally {
    writing = false;
    renderDraft();
  }
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
      // Keep the version-mismatch notice visible: rendering draft state with no
      // compatible board would replace its actionable redeploy instruction.
      if (result !== "drift") await loadDraft();
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

viewTabsEl.addEventListener("click", (e) => {
  const button = (e.target as HTMLElement).closest<HTMLButtonElement>("[data-view]");
  if (!button) return;
  view = button.dataset.view === "drafted" ? "drafted" : "available";
  viewTabsEl.querySelectorAll<HTMLButtonElement>("[data-view]").forEach((tab) => tab.setAttribute("aria-pressed", String(tab.dataset.view === view)));
  renderList();
});

teamNamesEl.addEventListener("input", refreshUserTeams);
saveDraftEl.addEventListener("click", () => {
  const names = teamNamesEl.value.split("\n").map((name) => name.trim()).filter(Boolean);
  void writeDraft("/api/draft", "PUT", {
    name: draftNameEl.value.trim(),
    rounds: Number(roundsEl.value),
    teams: names.map((name) => ({ name, is_user: name === userTeamEl.value })),
  });
});

suggestionsEl.addEventListener("click", (e) => {
  const button = (e.target as HTMLElement).closest<HTMLButtonElement>("[data-player-key]");
  if (!button) return;
  setSelected(playerByKey(decodeURIComponent(button.dataset.playerKey ?? "")));
});

playerSearchEl.addEventListener("input", () => {
  const picked = new Set(draft?.picks.map((pick) => pick.player_key) ?? []);
  const results = board ? searchPlayers(board.players, picked, playerSearchEl.value) : [];
  playerSearchEl.setAttribute("aria-expanded", String(results.length > 0));
  searchResultsEl.innerHTML = results.map((player) => `<button class="searchresult" role="option" data-player-key="${encodeURIComponent(player.key)}"><b>${escapeHtml(player.name)}</b> <small>${escapeHtml(player.pos ?? "—")} · ${escapeHtml(player.team ?? "FA")} · ADP ${player.adp ?? "—"}</small></button>`).join("");
});

searchResultsEl.addEventListener("click", (e) => {
  const button = (e.target as HTMLElement).closest<HTMLButtonElement>("[data-player-key]");
  if (!button) return;
  const player = playerByKey(decodeURIComponent(button.dataset.playerKey ?? ""));
  setSelected(player);
  playerSearchEl.value = player?.name ?? "";
  playerSearchEl.setAttribute("aria-expanded", "false");
  searchResultsEl.innerHTML = "";
});

recordPickEl.addEventListener("click", () => {
  if (!selected || !draft?.next) return;
  void writeDraft("/api/picks", "POST", { player_key: selected.key, expected_overall_pick: draft.next.overall_pick });
});

undoPickEl.addEventListener("click", () => {
  const latest = draft?.picks.at(-1);
  if (latest) void writeDraft("/api/picks/latest", "DELETE", { expected_overall_pick: latest.overall_pick });
});

resetDraftEl.addEventListener("click", () => {
  if (window.confirm("Reset this draft? This permanently removes all recorded picks and teams.")) void writeDraft("/api/draft", "DELETE");
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
  // A draft cannot be rendered safely against an incompatible board. More
  // importantly, avoid overwriting the contract-drift notice from loadBoard.
  const draftResult = result === "drift" ? null : await loadDraft();
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
    if (draftResult === "unauthorized") {
      store.del();
      dispatch({ type: "invalid" });
    } else if (draftResult === "network") {
      setDraftError("Network error — draft state could not load.");
    }
  }
}

void boot();
