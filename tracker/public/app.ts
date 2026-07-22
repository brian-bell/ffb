// Client boot: reads the key from the store, gates the board behind the key
// modal, fetches /api/board, and renders by position → tier. The testable core
// (the reducer, the store, the renderer) lives in ../src; this file is the thin
// DOM wiring around them (slice-6 §3c/§6).

import { renderBoard } from "../src/render";
import { isValidBoard, validateVersion } from "../src/board";
import { makeStore, nextState, resetsKeyInput, initialState, type UiState, type UiEvent } from "../src/state";
import { searchPlayers, suggestPlayers } from "../src/suggestions";
import { setupValidation, teamsFromSetup } from "../src/setup";
import {
  boardNoticeHtml,
  describeBoardView,
  initialBoardView,
  nextBoardView,
  type BoardPosition,
  type BoardViewState,
} from "../src/board-view";
import { deriveRecommendation } from "../src/recommendation";
import { needsHtml, recommendationHtml, recommendationSummaryHtml } from "../src/recommendation-view";
import { isAvailable, playersEquivalent } from "../src/player-identity";
import type { DraftState } from "../src/draft-store";
import type { Board, Player } from "../src/types";

const POSITIONS: BoardPosition[] = ["ALL", "QB", "RB", "WR", "TE", "K", "DEF"];

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
const orderLabelEl = $<HTMLElement>("[data-order-label]");
const viewLeadEl = $<HTMLElement>("[data-view-lead]");
const viewDetailEl = $<HTMLElement>("[data-view-detail]");
const setupModalEl = $<HTMLElement>("[data-setup-modal]");
const setupEl = $<HTMLElement>("[data-setup]");
const draftNameEl = $<HTMLInputElement>("[data-draft-name]");
const roundsEl = $<HTMLInputElement>("[data-rounds]");
const teamNamesEl = $<HTMLTextAreaElement>("[data-team-names]");
const userTeamEl = $<HTMLElement>("[data-user-team]") as unknown as HTMLSelectElement;
const saveDraftEl = $<HTMLButtonElement>("[data-save-draft]");
const pickPanelEl = $<HTMLElement>("[data-pick-panel]");
const pickToolsToggleEl = $<HTMLButtonElement>("[data-pick-tools-toggle]");
const pickToolsEl = $<HTMLElement>("[data-pick-tools]");
const onClockEl = $<HTMLElement>("[data-on-clock]");
const recommendationSummaryEl = $<HTMLElement>("[data-recommendation-summary]");
const suggestionsEl = $<HTMLElement>("[data-suggestions]");
const recommendationRegionEl = $<HTMLElement>("[data-recommendation-region]");
const recommendationEl = $<HTMLElement>("[data-recommendation]");
const needsEl = $<HTMLElement>("[data-needs]");
const recommendationWarningEl = $<HTMLElement>("[data-recommendation-warning]");
const recommendationLiveEl = $<HTMLElement>("[data-recommendation-live]");
const playerSearchEl = $<HTMLInputElement>("[data-player-search]");
const searchResultsEl = $<HTMLElement>("[data-search-results]");
const unlistedEl = $<HTMLButtonElement>("[data-unlisted]");
const manualFormEl = $<HTMLFormElement>("[data-manual-form]");
const manualNameEl = $<HTMLInputElement>("[data-manual-name]");
const manualPosEl = $<HTMLElement>("[data-manual-pos]") as unknown as HTMLSelectElement;
const manualTeamEl = $<HTMLInputElement>("[data-manual-team]");
const manualCancelEl = $<HTMLButtonElement>("[data-manual-cancel]");
const selectedEl = $<HTMLElement>("[data-selected]");
const draftErrorEl = $<HTMLElement>("[data-draft-error]");
const setupErrorEl = $<HTMLElement>("[data-setup-error]");
const setupStatusEl = $<HTMLElement>("[data-setup-status]");
const recordPickEl = $<HTMLButtonElement>("[data-record-pick]");
const undoPickEl = $<HTMLButtonElement>("[data-undo-pick]");
const resetDraftEl = $<HTMLButtonElement>("[data-reset-draft]");

// ---- app state ----
let ui: UiState = initialState;
let board: Board | null = null;
let boardDriftVersion: unknown | null = null;
let boardMalformed = false;
let boardView: BoardViewState = initialBoardView;
let draft: DraftState | null = null;
interface ManualPlayerInput { name: string; pos: "QB" | "RB" | "WR" | "TE" | "K" | "DEF" | "DST" | "Unknown"; team: string | null; }
type PickSelection = { kind: "board"; player: Player } | { kind: "manual"; player: ManualPlayerInput };
let selected: PickSelection | null = null;
let writing = false;
let setupPresented = false;
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
}

// ---- board rendering ----
function renderList(): void {
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
  listEl.innerHTML = renderBoard(board, boardView.position, {
    picked,
    mode: boardView.mode,
    draftPicks: draft?.picks,
  });
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

function setSelected(selection: PickSelection | null): void {
  selected = selection;
  const player = selection?.player;
  selectedEl.innerHTML = player ? `Selected: <b>${escapeHtml(player.name)}</b> · ${escapeHtml(player.pos ?? "—")} · ${escapeHtml(player.team ?? "FA")}` : "No player selected.";
  recordPickEl.disabled = !selection || !draft?.next || writing;
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
  const selectedName = userTeamEl.value;
  const names = teamNamesEl.value.split("\n").map((name) => name.trim()).filter(Boolean);
  userTeamEl.innerHTML = names.map((name) => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`).join("");
  if (names.includes(selectedName)) userTeamEl.value = selectedName;
}

function renderDraft(): void {
  const configured = draft?.configured === true;
  const showSetup = !configured && !ui.locked;
  setupModalEl.hidden = !showSetup;
  screenEl.toggleAttribute("inert", showSetup);
  screenEl.setAttribute("aria-hidden", String(showSetup));
  pickPanelEl.hidden = !configured;
  renderPickTools();
  if (!configured) {
    clockEl.textContent = "Set up draft";
    subEl.textContent = board ? `${board.num_teams}-team · ${board.scoring} scoring` : "—";
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
    onClockEl.textContent = "Draft complete — undo the latest pick to reopen it.";
  } else {
    const pick = `${next.round}.${String(next.round_pick).padStart(2, "0")}`;
    clockEl.textContent = `${pick} · ${next.is_user ? "YOU · " : ""}${next.team_name} ${next.direction === "forward" ? "↓" : "↑"}`;
    onClockEl.textContent = `Round ${next.round} of ${draft!.draft!.rounds} · Pick ${next.overall_pick} of ${draft!.draft!.team_count * draft!.draft!.rounds} · ${next.team_name} is on the clock`;
  }
  const picked = draft!.picks.map((pick) => ({ key: pick.player_key, name: pick.player_name, pos: pick.player_pos, team: pick.player_team }));
  const recommendation = board ? deriveRecommendation(board, draft!) : null;
  recommendationSummaryEl.innerHTML = recommendationSummaryHtml(
    recommendation ?? { context: null, recommendation: null, warnings: [] },
  );
  recommendationRegionEl.hidden = !next?.is_user;
  recommendationEl.innerHTML = recommendation ? recommendationHtml(recommendation) : "";
  needsEl.innerHTML = recommendation ? needsHtml(recommendation) : "";
  recommendationWarningEl.textContent = recommendation?.warnings.join(" ") ?? "";
  recommendationLiveEl.textContent = recommendation?.recommendation ? `${recommendation.recommendation.position} ${recommendation.recommendation.player.name}: ${recommendation.recommendation.reason}` : "";
  suggestionsEl.innerHTML = !next || !board ? "" : suggestPlayers(board.players, picked)
    .map((player) => `<button class="suggestion" data-player-key="${encodeURIComponent(player.key)}"><b>${escapeHtml(player.name)}</b><small>${escapeHtml(player.pos ?? "—")} · ${escapeHtml(player.team ?? "FA")} · ADP ${player.adp ?? "—"}</small></button>`)
    .join("");
  const canPick = Boolean(next && board);
  playerSearchEl.disabled = !canPick || writing;
  unlistedEl.disabled = !next || writing;
  if (selected?.kind === "board" && !isAvailable(selected.player, picked)) selected = null;
  undoPickEl.disabled = writing || draft!.picks.length === 0;
  undoPickEl.textContent = draft!.picks.length ? `Undo ${draft!.picks.at(-1)!.round}.${String(draft!.picks.at(-1)!.round_pick).padStart(2, "0")} — ${draft!.picks.at(-1)!.player_name}` : "Undo latest pick";
  setSelected(next ? selected : null);
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
  tabsEl.querySelectorAll<HTMLButtonElement>("[data-pos]").forEach((tab) => {
    tab.setAttribute("aria-selected", String(tab.dataset.pos === boardView.position));
  });
  viewTabsEl.querySelectorAll<HTMLButtonElement>("[data-view]").forEach((tab) => {
    tab.setAttribute("aria-pressed", String(tab.dataset.view === boardView.mode));
  });
  const description = describeBoardView(boardView);
  orderLabelEl.textContent = description.orderLabel;
  viewLeadEl.textContent = description.lead;
  viewDetailEl.textContent = description.detail;
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
    if (path === "/api/picks" && method === "POST") {
      boardView = nextBoardView(boardView, { type: "pickRecorded" });
    }
    if (!wasConfigured && draft.configured) focusPickTools = true;
    selected = null;
    playerSearchEl.value = "";
    searchResultsEl.innerHTML = "";
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
  if (!btn) return;
  boardView = nextBoardView(boardView, {
    type: "selectPosition",
    position: (btn.dataset.pos ?? "ALL") as BoardPosition,
  });
  renderViewControls();
  renderList();
});

viewTabsEl.addEventListener("click", (e) => {
  const button = (e.target as HTMLElement).closest<HTMLButtonElement>("[data-view]");
  if (!button) return;
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

teamNamesEl.addEventListener("input", refreshUserTeams);
teamNamesEl.addEventListener("input", () => {
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

suggestionsEl.addEventListener("click", (e) => {
  const button = (e.target as HTMLElement).closest<HTMLButtonElement>("[data-player-key]");
  if (!button) return;
  const player = playerByKey(decodeURIComponent(button.dataset.playerKey ?? ""));
  setSelected(player ? { kind: "board", player } : null);
});

recommendationEl.addEventListener("click", (e) => {
  const button = (e.target as HTMLElement).closest<HTMLButtonElement>("[data-recommendation-key]");
  if (!button) return;
  const player = playerByKey(decodeURIComponent(button.dataset.recommendationKey ?? ""));
  setSelected(player ? { kind: "board", player } : null);
});

playerSearchEl.addEventListener("input", () => {
  const picked = draft?.picks.map((pick) => ({ key: pick.player_key, name: pick.player_name, pos: pick.player_pos, team: pick.player_team })) ?? [];
  const results = board ? searchPlayers(board.players, picked, playerSearchEl.value) : [];
  playerSearchEl.setAttribute("aria-expanded", String(results.length > 0));
  searchResultsEl.innerHTML = results.map((player) => `<button class="searchresult" role="option" data-player-key="${encodeURIComponent(player.key)}"><b>${escapeHtml(player.name)}</b> <small>${escapeHtml(player.pos ?? "—")} · ${escapeHtml(player.team ?? "FA")} · ADP ${player.adp ?? "—"}</small></button>`).join("");
});

searchResultsEl.addEventListener("click", (e) => {
  const button = (e.target as HTMLElement).closest<HTMLButtonElement>("[data-player-key]");
  if (!button) return;
  const player = playerByKey(decodeURIComponent(button.dataset.playerKey ?? ""));
  setSelected(player ? { kind: "board", player } : null);
  playerSearchEl.value = player?.name ?? "";
  playerSearchEl.setAttribute("aria-expanded", "false");
  searchResultsEl.innerHTML = "";
});

unlistedEl.addEventListener("click", () => {
  manualFormEl.hidden = !manualFormEl.hidden;
  if (!manualFormEl.hidden) manualNameEl.focus();
});

manualFormEl.addEventListener("submit", (event) => {
  event.preventDefault();
  const name = manualNameEl.value.trim();
  const pos = manualPosEl.value as ManualPlayerInput["pos"];
  const team = manualTeamEl.value.trim() || null;
  if (!name) {
    setDraftError("Enter the unlisted player's exact display name.");
    manualNameEl.focus();
    return;
  }
  setSelected({ kind: "manual", player: { name, pos, team } });
  manualFormEl.hidden = true;
  setDraftError("");
});

manualCancelEl.addEventListener("click", () => {
  manualFormEl.hidden = true;
  playerSearchEl.focus();
});

recordPickEl.addEventListener("click", () => {
  if (!selected || !draft?.next) return;
  const payload = selected.kind === "board"
    ? { player_key: selected.player.key, expected_overall_pick: draft.next.overall_pick }
    : { manual_player: selected.player, expected_overall_pick: draft.next.overall_pick };
  void writeDraft("/api/picks", "POST", payload);
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
