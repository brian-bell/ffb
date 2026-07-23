// Client boot: reads the key from the store, gates the board behind the key
// modal, fetches /api/board, and renders by position → tier. The testable core
// (the reducer, the store, the renderer) lives in ../src; this file is the thin
// DOM wiring around them (slice-6 §3c/§6).

import { renderBoard } from "../src/render";
import { isValidBoard, validateVersion } from "../src/board";
import { makeStore, nextState, resetsKeyInput, initialState, type UiState, type UiEvent } from "../src/state";
import { searchPlayers } from "../src/suggestions";
import { makeSetupStore, nextSetupDialog, setupValidation, teamOptionsFromSetup, teamsFromSetup } from "../src/setup";
import {
  boardNoticeHtml,
  initialBoardView,
  nextBoardView,
  type BoardPosition,
  type BoardViewState,
} from "../src/board-view";
import { isAvailable, playersEquivalent } from "../src/player-identity";
import { draftClockPresentation, draftPageTitle, nextPick } from "../src/draft";
import type { DraftConfigInput, DraftState } from "../src/draft-store";
import type { Board, Player } from "../src/types";

const POSITIONS: BoardPosition[] = ["ALL", "QB", "RB", "WR", "TE", "K", "DEF"];

function localStorageOrNull(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

const browserStorage = localStorageOrNull();
const store = makeStore(browserStorage);
const setupStore = makeSetupStore(browserStorage);

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
const clockEl = $<HTMLElement>("[data-clock]");
const setupDraftEl = $<HTMLButtonElement>("[data-setup-draft]");
const viewTabsEl = $<HTMLElement>("[data-view-tabs]");
const setupModalEl = $<HTMLElement>("[data-setup-modal]");
const setupEl = $<HTMLElement>("[data-setup]");
const closeSetupEl = $<HTMLButtonElement>("[data-close-setup]");
const draftNameEl = $<HTMLInputElement>("[data-draft-name]");
const roundsEl = $<HTMLInputElement>("[data-rounds]");
const teamNamesEl = $<HTMLTextAreaElement>("[data-team-names]");
const userTeamEl = $<HTMLElement>("[data-user-team]") as unknown as HTMLSelectElement;
const saveDraftEl = $<HTMLButtonElement>("[data-save-draft]");
const pickPanelEl = $<HTMLElement>("[data-pick-panel]");
const pickToolsToggleEl = $<HTMLButtonElement>("[data-pick-tools-toggle]");
const pickToolsEl = $<HTMLElement>("[data-pick-tools]");
const onClockEl = $<HTMLElement>("[data-on-clock]");
const playerSearchEl = $<HTMLInputElement>("[data-player-search]");
const selectedEl = $<HTMLElement>("[data-selected]");
const draftErrorEl = $<HTMLElement>("[data-draft-error]");
const setupErrorEl = $<HTMLElement>("[data-setup-error]");
const setupStatusEl = $<HTMLElement>("[data-setup-status]");
const recordPickEl = $<HTMLButtonElement>("[data-record-pick]");
const clearPickEl = $<HTMLButtonElement>("[data-clear-pick]");
const undoPickEl = $<HTMLButtonElement>("[data-undo-pick]");
const resetDraftEl = $<HTMLButtonElement>("[data-reset-draft]");

// ---- app state ----
let ui: UiState = initialState;
let board: Board | null = null;
let boardDriftVersion: unknown | null = null;
let boardMalformed = false;
let boardView: BoardViewState = initialBoardView;
let draft: DraftState | null = null;
let writing = false;
let setupPresented = false;
let setupOpen = false;
let focusPickTools = false;

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
  renderSettingsDraftAction();
}

// ---- board rendering ----
function pickedIdentities(): Array<{ key: string; name: string; pos: string | null; team: string | null }> {
  return draft?.picks.map((pick) => ({
    key: pick.player_key,
    name: pick.player_name,
    pos: pick.player_pos,
    team: pick.player_team,
  })) ?? [];
}

function renderList(resetScroll = true): void {
  if (!board) {
    listEl.innerHTML = boardNoticeHtml(boardDriftVersion, boardMalformed);
    return;
  }
  const picked = new Map<string, { overall_pick: number; round: number; round_pick: number; team_name: string }>();
  for (const pick of draft?.picks ?? []) {
    const annotation = { overall_pick: pick.overall_pick, round: pick.round, round_pick: pick.round_pick, team_name: pick.team_name };
    for (const player of board.players) {
      if (playersEquivalent(player, { key: pick.player_key, name: pick.player_name, pos: pick.player_pos, team: pick.player_team })) picked.set(player.key, annotation);
    }
  }
  const searching = boardView.searchQuery.trim().length > 0 && Boolean(draft?.next);
  const searchResults = searching
    ? searchPlayers(board.players, pickedIdentities(), boardView.searchQuery)
    : undefined;
  listEl.innerHTML = renderBoard(board, boardView.position, {
    picked,
    mode: boardView.mode,
    draftPicks: draft?.picks,
    selectable: Boolean(draft?.next) && !writing,
    selectedKey: boardView.selectedKey,
    searchResults,
  });
  if (resetScroll) listEl.scrollTop = 0;
}

function renderChrome(): void {
  if (!board) return;
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

function playerByKey(key: string | null): Player | null {
  if (key === null) return null;
  return board?.players.find((player) => player.key === key) ?? null;
}

function renderSelection(): void {
  const player = playerByKey(boardView.selectedKey);
  selectedEl.innerHTML = player
    ? `<b>${escapeHtml(player.name)}</b> · ${escapeHtml(player.pos ?? "—")} · ${escapeHtml(player.team ?? "FA")}`
    : "No Player Selected.";
  recordPickEl.disabled = !player || !draft?.next || writing;
  clearPickEl.disabled = !player || !draft?.next || writing;
}

function renderSettingsDraftAction(): void {
  resetDraftEl.hidden = ui.modal !== "settings" || draft?.configured !== true;
  resetDraftEl.disabled = writing;
}

function setDraftError(message = ""): void {
  draftErrorEl.textContent = message;
}

function setSetupError(message = ""): void {
  setupErrorEl.hidden = !message;
  setupErrorEl.textContent = message;
}

function setSetupStatus(message = ""): void {
  setupStatusEl.hidden = !message;
  setupStatusEl.textContent = message;
}

function clearSetupInvalid(): void {
  teamNamesEl.removeAttribute("aria-invalid");
  userTeamEl.removeAttribute("aria-invalid");
  roundsEl.removeAttribute("aria-invalid");
}

function refreshUserTeams(): void {
  const options = teamOptionsFromSetup(teamNamesEl.value, userTeamEl.value).map(({ name, selected }) => {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    option.selected = selected;
    return option;
  });
  userTeamEl.replaceChildren(...options);
}

function setupFromDraftState(state: DraftState): DraftConfigInput | null {
  if (!state.configured || !state.draft || !state.teams) return null;
  return {
    name: state.draft.name,
    rounds: state.draft.rounds,
    teams: state.teams.map((team) => ({ name: team.name, is_user: team.is_user })),
  };
}

function populateSetup(config: DraftConfigInput): void {
  draftNameEl.value = config.name;
  roundsEl.value = String(config.rounds);
  teamNamesEl.value = config.teams.map((team) => team.name).join("\n");
  refreshUserTeams();
  userTeamEl.value = config.teams.find((team) => team.is_user)?.name ?? "";
  clearSetupInvalid();
  setSetupError("");
}

function renderDraft(): void {
  const configured = draft?.configured === true;
  document.title = draftPageTitle(draft?.draft?.name ?? null, Boolean(draft?.next));
  renderSettingsDraftAction();
  const showSetup = setupOpen && !configured && !ui.locked;
  setupModalEl.hidden = !showSetup;
  screenEl.toggleAttribute("inert", showSetup);
  screenEl.setAttribute("aria-hidden", String(showSetup));
  setupDraftEl.hidden = configured;
  clockEl.hidden = !configured;
  pickPanelEl.hidden = !configured;
  renderPickTools();
  if (!configured) {
    renderList();
    if (showSetup && !setupPresented) setTimeout(() => draftNameEl.focus(), 0);
    setupPresented = showSetup;
    saveDraftEl.disabled = writing;
    saveDraftEl.textContent = writing ? "Saving draft setup…" : "Save draft setup";
    setupEl.setAttribute("aria-busy", String(writing));
    return;
  }
  setupPresented = false;
  const next = draft!.next;
  if (!next) {
    clockEl.textContent = "Draft complete";
    clockEl.removeAttribute("aria-label");
    onClockEl.textContent = "Draft complete — undo the latest pick to reopen it.";
  } else {
    const followingPick = nextPick(draft!.teams!, draft!.draft!.rounds, next.overall_pick + 1);
    const presentation = draftClockPresentation(next, followingPick?.team_name ?? null);
    const summaryEl = document.createElement("span");
    const nextEl = document.createElement("span");
    const accessibleEl = document.createElement("span");
    summaryEl.textContent = presentation.current;
    summaryEl.setAttribute("aria-hidden", "true");
    nextEl.className = "next-team";
    nextEl.textContent = presentation.next;
    nextEl.title = followingPick ? `Next: ${followingPick.team_name}` : "Next: Draft complete";
    nextEl.setAttribute("aria-hidden", "true");
    accessibleEl.className = "sr-only";
    accessibleEl.textContent = presentation.accessible;
    clockEl.replaceChildren(summaryEl, nextEl, accessibleEl);
    clockEl.removeAttribute("aria-label");
    onClockEl.textContent = `Round ${next.round} of ${draft!.draft!.rounds} · Pick ${next.overall_pick} of ${draft!.draft!.team_count * draft!.draft!.rounds} · ${next.team_name} is on the clock`;
  }
  const picked = pickedIdentities();
  const canPick = Boolean(next && board);
  playerSearchEl.disabled = !canPick || writing;
  const selectedPlayer = playerByKey(boardView.selectedKey);
  if (selectedPlayer && !isAvailable(selectedPlayer, picked)) {
    boardView = nextBoardView(boardView, { type: "selectionCleared" });
  }
  undoPickEl.disabled = writing || draft!.picks.length === 0;
  const undoLabel = draft!.picks.length
    ? `Undo ${draft!.picks.at(-1)!.round}.${String(draft!.picks.at(-1)!.round_pick).padStart(2, "0")} — ${draft!.picks.at(-1)!.player_name}`
    : "Undo latest pick";
  undoPickEl.setAttribute("aria-label", undoLabel);
  undoPickEl.title = undoLabel;
  undoPickEl.dataset.tooltip = undoLabel;
  renderSelection();
  renderViewControls();
  renderList();
  if (focusPickTools) {
    focusPickTools = false;
    setTimeout(() => pickToolsToggleEl.focus(), 0);
  }
}

function showContractDrift(got: unknown): void {
  board = null;
  boardMalformed = false;
  boardDriftVersion = got;
  renderList();
}

function renderViewControls(): void {
  if (!tabsEl.children.length) {
    tabsEl.innerHTML = POSITIONS.map(
      (position) => `<button class="tab" role="tab" data-pos="${position}">${position}</button>`,
    ).join("");
  }
  const searching = boardView.searchQuery.trim().length > 0 && Boolean(draft?.next);
  tabsEl.querySelectorAll<HTMLButtonElement>("[data-pos]").forEach((tab) => {
    tab.setAttribute("aria-selected", String(tab.dataset.pos === boardView.position));
    tab.disabled = searching;
  });
  viewTabsEl.querySelectorAll<HTMLButtonElement>("[data-view]").forEach((tab) => {
    const presentedMode = searching ? "available" : boardView.mode;
    tab.setAttribute("aria-pressed", String(tab.dataset.view === presentedMode));
    tab.disabled = searching;
  });
  playerSearchEl.value = boardView.searchQuery;
}

function renderPickTools(): void {
  pickToolsToggleEl.setAttribute("aria-expanded", String(boardView.pickToolsExpanded));
  pickToolsToggleEl.textContent = boardView.pickToolsExpanded ? "Close" : "Pick tools";
  pickToolsEl.hidden = !boardView.pickToolsExpanded;
}

// ---- data fetch ----
type LoadResult = "ok" | "unauthorized" | "empty" | "network" | "drift" | "malformed";

async function loadBoard(key: string): Promise<LoadResult> {
  boardDriftVersion = null;
  boardMalformed = false;
  let res: Response;
  try {
    res = await fetch("/api/board", { headers: { Authorization: `Bearer ${key}` } });
  } catch {
    return "network";
  }
  if (res.status === 401) return "unauthorized";
  if (res.status === 404) return "empty";
  if (!res.ok) return "network";

  let parsed: unknown;
  try {
    parsed = await res.json();
  } catch {
    return "network";
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    board = null;
    boardMalformed = true;
    renderList();
    return "malformed";
  }
  if (!validateVersion(parsed)) {
    showContractDrift((parsed as { version?: unknown }).version);
    return "drift";
  }
  if (!isValidBoard(parsed)) {
    board = null;
    boardMalformed = true;
    renderList();
    return "malformed";
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
    const currentSetup = setupFromDraftState(draft);
    if (currentSetup) setupStore.set(currentSetup);
    renderDraft();
    return "ok";
  } catch {
    return "network";
  }
}

async function writeDraft(path: string, method: string, payload?: unknown): Promise<boolean> {
  if (writing) return false;
  const wasConfigured = draft?.configured === true;
  writing = true;
  if (!draft?.configured) setSetupStatus("Saving draft setup…");
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
      const message = response.message ?? response.error ?? "Could not update draft.";
      if (draft?.configured) setDraftError(message);
      else setSetupError(message);
      if (res.status === 409) await loadDraft();
      return false;
    }
    draft = response;
    if (path === "/api/draft" && method === "PUT" && draft.configured) {
      const currentSetup = setupFromDraftState(draft);
      if (currentSetup) setupStore.set(currentSetup);
      setupOpen = nextSetupDialog(setupOpen, { type: "close" });
    } else if (path === "/api/draft" && method === "DELETE") {
      setupOpen = nextSetupDialog(setupOpen, { type: "draftReset" });
      boardView = initialBoardView;
    } else if (path === "/api/picks" && method === "POST") {
      boardView = nextBoardView(boardView, { type: "pickRecorded" });
    }
    if (!wasConfigured && draft.configured) focusPickTools = true;
    setDraftError("");
    setSetupError("");
    renderDraft();
    return true;
  } catch {
    const message = "Network error — draft state was not changed here.";
    if (draft?.configured) setDraftError(message);
    else setSetupError(message);
    return false;
  } finally {
    writing = false;
    setSetupStatus("");
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
    case "malformed":
    case "empty":
      // The key authenticated (even a 404/drift means the gate passed); persist + unlock.
      store.set(key);
      dispatch({ type: "unlock" });
      // Keep the version-mismatch notice visible: rendering draft state with no
      // compatible board would replace its actionable redeploy instruction.
      if (result !== "drift" && result !== "malformed") await loadDraft();
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
  if (!btn || btn.disabled) return;
  boardView = nextBoardView(boardView, {
    type: "selectPosition",
    position: (btn.dataset.pos ?? "ALL") as BoardPosition,
  });
  renderViewControls();
  renderList();
});

viewTabsEl.addEventListener("click", (e) => {
  const button = (e.target as HTMLElement).closest<HTMLButtonElement>("[data-view]");
  if (!button || button.disabled) return;
  boardView = nextBoardView(boardView, {
    type: "selectMode",
    mode: button.dataset.view === "drafted" ? "drafted" : "available",
  });
  renderViewControls();
  renderList();
});

pickToolsToggleEl.addEventListener("click", () => {
  boardView = nextBoardView(boardView, { type: "togglePickTools" });
  renderPickTools();
});

setupDraftEl.addEventListener("click", () => {
  if (draft?.configured) return;
  const rememberedSetup = setupStore.get();
  if (rememberedSetup) populateSetup(rememberedSetup);
  setupOpen = nextSetupDialog(setupOpen, { type: "open" });
  renderDraft();
});

closeSetupEl.addEventListener("click", () => {
  setupOpen = nextSetupDialog(setupOpen, { type: "close" });
  renderDraft();
  setupDraftEl.focus();
});

teamNamesEl.addEventListener("input", () => {
  refreshUserTeams();
  clearSetupInvalid();
  setSetupError("");
});
userTeamEl.addEventListener("change", () => {
  clearSetupInvalid();
  setSetupError("");
});
roundsEl.addEventListener("input", () => {
  clearSetupInvalid();
  setSetupError("");
});
setupEl.addEventListener("submit", (event) => {
  event.preventDefault();
  if (writing) return;
  const validationError = setupValidation(teamNamesEl.value, userTeamEl.value, Number(roundsEl.value));
  if (validationError) {
    clearSetupInvalid();
    const field = validationError.field === "teams" ? teamNamesEl : validationError.field === "rounds" ? roundsEl : userTeamEl;
    field.setAttribute("aria-invalid", "true");
    setSetupError(validationError.message);
    field.focus();
    return;
  }
  clearSetupInvalid();
  setSetupError("");
  void writeDraft("/api/draft", "PUT", {
    name: draftNameEl.value.trim(),
    rounds: Number(roundsEl.value),
    teams: teamsFromSetup(teamNamesEl.value, userTeamEl.value),
  });
});

setupEl.addEventListener("keydown", (event) => {
  if (event.key !== "Tab") return;
  const focusable = [...setupEl.querySelectorAll<HTMLElement>("input, textarea, select, button:not([disabled])")];
  const first = focusable[0];
  const last = focusable.at(-1);
  if (!first || !last) return;
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
});

listEl.addEventListener("click", (event) => {
  const button = (event.target as HTMLElement).closest<HTMLButtonElement>("button[data-player-key]");
  if (!button || !draft?.next || writing) return;
  const key = decodeURIComponent(button.dataset.playerKey ?? "");
  if (!playerByKey(key)) return;
  boardView = nextBoardView(boardView, { type: "playerSelected", key });
  renderPickTools();
  renderSelection();
  renderList(false);
  listEl.querySelector<HTMLButtonElement>(`button[data-player-key="${encodeURIComponent(key)}"]`)?.focus({ preventScroll: true });
});

playerSearchEl.addEventListener("input", () => {
  boardView = nextBoardView(boardView, { type: "searchChanged", query: playerSearchEl.value });
  renderViewControls();
  renderList();
});

recordPickEl.addEventListener("click", () => {
  const player = playerByKey(boardView.selectedKey);
  if (!player || !draft?.next) return;
  void writeDraft("/api/picks", "POST", {
    player_key: player.key,
    expected_overall_pick: draft.next.overall_pick,
  });
});

clearPickEl.addEventListener("click", () => {
  if (boardView.selectedKey === null || writing) return;
  boardView = nextBoardView(boardView, { type: "selectionCleared" });
  renderSelection();
  renderList(false);
});

undoPickEl.addEventListener("click", () => {
  const latest = draft?.picks.at(-1);
  if (latest) void writeDraft("/api/picks/latest", "DELETE", { expected_overall_pick: latest.overall_pick });
});

resetDraftEl.addEventListener("click", () => {
  if (!window.confirm("Reset this draft? This permanently removes all recorded picks and teams.")) return;
  void writeDraft("/api/draft", "DELETE").then((reset) => {
    if (reset && store.get()) dispatch({ type: "closeModal" });
  });
});

// ---- first run vs. returning device ----
async function boot(): Promise<void> {
  renderViewControls();
  const key = store.get();
  if (!key) {
    dispatch({ type: "boot", hasKey: false });
    renderList();
    return;
  }
  const result = await loadBoard(key);
  // A draft cannot be rendered safely against an incompatible board. More
  // importantly, avoid overwriting the contract-drift notice from loadBoard.
  const draftResult = result === "drift" || result === "malformed" ? null : await loadDraft();
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
