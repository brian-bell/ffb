import { isValidBoard } from "../src/board";
import type { MockState } from "../src/mock-draft";
import { initialBoardView, nextBoardView, type BoardPosition, type BoardViewState } from "../src/board-view";
import { reconcileMockBoardView } from "../src/mock-view";
import {
  mockClockState,
  mockActionState,
  mockErrorMessage,
  mockSuggestions,
  readMockSetupControls,
  renderMockError,
  renderVariancePreset,
} from "../src/mock-ui";
import { buildPlayerPool, type PlayerPool } from "../src/player-pool";
import { renderBoard } from "../src/render";
import { requestJson } from "../src/request-json";
import { makeStore } from "../src/state";
import type { Board, Player } from "../src/types";

function element<T extends Element>(selector: string): T {
  const found = document.querySelector<T>(selector);
  if (!found) throw new Error(`missing ${selector}`);
  return found;
}

const screen = element<HTMLElement>("[data-screen]");
const lock = element<HTMLElement>("[data-lock]");
const unlockForm = element<HTMLFormElement>("[data-unlock-form]");
const keyInput = element<HTMLInputElement>("[data-key]");
const keyError = element<HTMLElement>("[data-key-error]");
const setupPanel = element<HTMLElement>("[data-mock-setup]");
const activePanel = element<HTMLElement>("[data-mock-active]");
const startForm = element<HTMLFormElement>("[data-start-form]");
const slotInput = element<HTMLElement>("[data-user-slot]") as unknown as HTMLSelectElement;
const seedInput = element<HTMLInputElement>("[data-seed]");
const varianceInput = element<HTMLElement>("[data-variance-preset]") as unknown as HTMLSelectElement;
const startButton = element<HTMLButtonElement>("[data-start]");
const setupError = element<HTMLElement>("[data-error]");
const tabs = element<HTMLElement>("[data-tabs]");
const viewTabs = element<HTMLElement>("[data-view-tabs]");
const playerSearch = element<HTMLInputElement>("[data-player-search]");
const columnHead = element<HTMLElement>("[data-colhead]");
const list = element<HTMLElement>("[data-list]");
const pickPanel = element<HTMLElement>("[data-pick-panel]");
const onClock = element<HTMLElement>("[data-on-clock]");
const clock = element<HTMLElement>("[data-clock]");
const selected = element<HTMLElement>("[data-selected]");
const draftPlayer = element<HTMLButtonElement>("[data-draft-player]");
const clearSelection = element<HTMLButtonElement>("[data-clear-selection]");
const pickToolsToggle = element<HTMLButtonElement>("[data-pick-tools-toggle]");
const pickTools = element<HTMLElement>("[data-pick-tools]");
const suggestions = element<HTMLElement>("[data-suggestions]");
const suggestionList = element<HTMLElement>("[data-suggestion-list]");
const transitionStatus = element<HTMLElement>("[data-transition-status]");
const lifecycleToggle = element<HTMLButtonElement>("[data-lifecycle-toggle]");
const undo = element<HTMLButtonElement>("[data-undo]");
const reset = element<HTMLButtonElement>("[data-reset]");
const discard = element<HTMLButtonElement>("[data-discard]");
const draftError = element<HTMLElement>("[data-draft-error]");
const events = element<HTMLElement>("[data-events]");
const foot = element<HTMLElement>("[data-foot]");
const teamCount = element<HTMLElement>("[data-team-count]");
const rounds = element<HTMLElement>("[data-rounds]");
const scoring = element<HTMLElement>("[data-scoring]");
const statusSeed = element<HTMLElement>("[data-status-seed]");
const statusVariance = element<HTMLElement>("[data-status-variance]");
const statusSlot = element<HTMLElement>("[data-status-slot]");
const statusRound = element<HTMLElement>("[data-status-round]");
const statusOverall = element<HTMLElement>("[data-status-overall]");
const statusLifecycle = element<HTMLElement>("[data-status-lifecycle]");
const statusRevision = element<HTMLElement>("[data-status-revision]");

function localStorageOrNull(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

const keyStore = makeStore(localStorageOrNull());
let board: Board | null = null;
let mock: MockState | null = null;
let boardView: BoardViewState = initialBoardView;
let writing = false;
let playerPool: PlayerPool | null = null;
let pooledPlayers: Board["players"] | null = null;
let pooledPicks: MockState["picks"] | null = null;

function authHeaders(json = false): HeadersInit {
  return {
    Authorization: `Bearer ${keyStore.get() ?? ""}`,
    ...(json ? { "content-type": "application/json" } : {}),
  };
}

function escaped(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function generatedSeed(): number {
  return crypto.getRandomValues(new Uint32Array(1))[0]!;
}

function boardRounds(value: Board): number {
  return Object.values(value.roster_slots).reduce(
    (total, count) => total + (count > 0 ? count : 0),
    0,
  );
}

function setLocked(locked: boolean, message = ""): void {
  screen.classList.toggle("locked", locked);
  lock.hidden = !locked;
  keyError.hidden = !message;
  keyError.textContent = message;
  if (locked) setTimeout(() => keyInput.focus(), 0);
}

async function api<T>(path: string, init: RequestInit = {}) {
  const result = await requestJson<T>(fetch, path, {
    ...init,
    headers: {
      ...authHeaders(init.body !== undefined),
      ...init.headers,
    },
  });
  if (result.response?.status === 401) {
    keyStore.del();
    setLocked(true, "Invalid API key. Check it and try again.");
  }
  return result;
}

function currentPlayerPool(): PlayerPool {
  if (!board) throw new Error("board unavailable");
  const picks = mock?.picks ?? [];
  if (playerPool === null || pooledPlayers !== board.players || pooledPicks !== picks) {
    playerPool = buildPlayerPool(board.players, picks);
    pooledPlayers = board.players;
    pooledPicks = picks;
  }
  return playerPool;
}

function selectedPlayer(): Player | null {
  return (
    currentPlayerPool().available.find((player) => player.key === boardView.selectedKey) ?? null
  );
}

function actions(boardAvailable = true) {
  const lifecycle = mock?.lifecycle ?? (mock?.complete ? "complete" : "active");
  return mockActionState({
    lifecycle,
    can_undo: mock?.can_undo === true,
    next: mock?.next ?? null,
    writing,
    board_available: boardAvailable,
  });
}

function renderTabs(): void {
  if (!board) return;
  const positions = [
    "ALL",
    ...new Set(board.players.map((player) => player.pos).filter((value): value is string => Boolean(value))),
  ];
  tabs.innerHTML = positions
    .map(
      (value) =>
        `<button class="tab" type="button" data-position="${escaped(value)}" aria-selected="${value === boardView.position}">${escaped(value)}</button>`,
    )
    .join("");
}

function renderEvents(): void {
  const latest = mock?.appended_picks ?? [];
  if (latest.length === 0) {
    events.innerHTML = "<span>No CPU picks in the latest transition.</span>";
    return;
  }
  events.innerHTML = latest
    .map((pick) => `<span><b>${pick.source === "user" ? "You" : escaped(pick.team_name)}</b> ` +
      `${pick.round}.${String(pick.round_pick).padStart(2, "0")} ${escaped(pick.player_name)}</span>`)
    .join(" · ");
}

function renderClock(): void {
  if (!mock?.mock || !mock.teams) {
    clock.hidden = true;
    return;
  }
  clock.hidden = false;
  const state = mockClockState(
    mock.lifecycle ?? (mock.complete ? "complete" : "active"),
    mock.teams,
    mock.mock.rounds,
    mock.next ?? null,
  );
  onClock.textContent = state.summary;
  if (!state.presentation) {
    clock.textContent = "Draft complete";
    return;
  }
  const current = document.createElement("span");
  const following = document.createElement("span");
  const accessible = document.createElement("span");
  current.textContent = state.presentation.current;
  current.setAttribute("aria-hidden", "true");
  following.className = "next-team";
  following.textContent = state.presentation.next;
  following.setAttribute("aria-hidden", "true");
  accessible.className = "sr-only";
  accessible.textContent = state.presentation.accessible;
  clock.replaceChildren(current, following, accessible);
}

function renderViewControls(): void {
  const searching = boardView.searchQuery.trim().length > 0;
  tabs.querySelectorAll<HTMLButtonElement>("[data-position]").forEach((tab) => {
    tab.setAttribute("aria-selected", String(tab.dataset.position === boardView.position));
    tab.disabled = searching;
  });
  viewTabs.querySelectorAll<HTMLButtonElement>("[data-view]").forEach((tab) => {
    tab.setAttribute("aria-pressed", String(tab.dataset.view === (searching ? "available" : boardView.mode)));
    tab.disabled = searching;
  });
  playerSearch.value = boardView.searchQuery;
}

function renderSuggestions(actionState: ReturnType<typeof actions>, pool: PlayerPool): void {
  suggestions.setAttribute("aria-busy", String(writing));
  if (!mock?.mock) return;
  const lifecycle = mock.lifecycle ?? (mock.complete ? "complete" : "active");
  const choices = mockSuggestions({
    board: board!, pool, picks: mock.picks, lifecycle,
    next: mock.next ?? null, user_slot: mock.mock.user_slot,
  });
  if (lifecycle === "paused") {
    suggestionList.textContent = "Resume to see suggestions";
  } else if (lifecycle === "complete" || mock.next === null) {
    suggestionList.textContent = "Draft complete";
  } else if (choices.length === 0) {
    suggestionList.textContent = "No legal suggestions";
  } else {
    suggestionList.innerHTML = choices.map((player) =>
      `<button type="button" class="mock-suggestion" data-player-key="${encodeURIComponent(player.key)}"${actionState.can_pick ? "" : " disabled"}>` +
      `<b>${escaped(player.name)}</b><span>${escaped(player.pos ?? "—")}/${escaped(player.team ?? "FA")}${player.adp === null ? "" : ` · ADP ${player.adp.toFixed(1)}`}</span></button>`
    ).join("");
  }
}

function observeLoadMore(): void {
  if (!loadMoreObserver) return;
  loadMoreObserver.disconnect();
  const sentinel = list.querySelector("[data-load-more]");
  if (sentinel) loadMoreObserver.observe(sentinel);
}

function loadMoreRows(): void {
  boardView = nextBoardView(boardView, { type: "loadMore" });
  renderState(false);
}

const loadMoreObserver = typeof IntersectionObserver === "undefined" ? null : new IntersectionObserver(
  (entries) => { if (entries.some((entry) => entry.isIntersecting)) loadMoreRows(); },
  { rootMargin: "600px" },
);

function renderState(resetScroll = true): void {
  if (!board) return;
  const configured = mock?.configured === true && mock.mock !== undefined;
  setupPanel.hidden = configured;
  activePanel.hidden = !configured;
  tabs.hidden = !configured;
  viewTabs.hidden = !configured;
  columnHead.hidden = !configured;
  list.hidden = !configured;
  pickPanel.hidden = !configured;
  clock.hidden = !configured;
  suggestions.hidden = !configured;

  teamCount.textContent = String(board.num_teams);
  rounds.textContent = String(boardRounds(board));
  scoring.textContent = board.scoring;
  foot.textContent = `board.json v${board.version} · ${board.generated_at}`;
  if (!configured || !mock?.mock) {
    clock.hidden = true;
    list.innerHTML =
      '<div class="notice"><b>Ready for an isolated rehearsal.</b>Choose a slot and seed to begin.</div>';
    return;
  }

  const next = mock.next ?? null;
  statusSeed.textContent = String(mock.mock.seed);
  renderVariancePreset(statusVariance, mock.mock.variance_preset);
  statusSlot.textContent = String(mock.mock.user_slot);
  statusRound.textContent = next ? String(next.round) : "Done";
  statusOverall.textContent = next ? String(next.overall_pick) : "Done";
  const actionState = actions();
  statusLifecycle.textContent = actionState.status_label;
  statusRevision.textContent = String(mock.revision);
  renderClock();
  const player = selectedPlayer();
  selected.innerHTML = player
    ? `<b>${escaped(player.name)}</b> · ${escaped(player.pos ?? "—")} · ${escaped(player.team ?? "FA")}`
    : "No Player Selected.";
  draftPlayer.disabled = !player || !actionState.can_pick;
  clearSelection.disabled = !player || !actionState.can_pick;
  pickToolsToggle.setAttribute("aria-expanded", String(boardView.pickToolsExpanded));
  pickToolsToggle.textContent = boardView.pickToolsExpanded ? "Close" : "Pick tools";
  pickTools.hidden = !boardView.pickToolsExpanded;
  lifecycleToggle.textContent = actionState.lifecycle_label;
  lifecycleToggle.disabled = !actionState.lifecycle_enabled;
  undo.disabled = !actionState.undo_enabled;
  reset.disabled = !actionState.reset_enabled;
  discard.disabled = !actionState.discard_enabled;
  renderEvents();
  renderTabs();

  const pool = currentPlayerPool();
  const searching = boardView.searchQuery.trim().length > 0;
  list.setAttribute("aria-busy", String(writing));
  list.innerHTML = renderBoard(board, boardView.position, {
    picked: pool.picked,
    mode: boardView.mode,
    draftPicks: mock.picks,
    selectable: actionState.can_pick,
    selectedKey: boardView.selectedKey,
    searchResults: searching ? pool.search(boardView.searchQuery) : undefined,
    window: { limit: boardView.visibleLimit },
  });
  if (resetScroll) list.scrollTop = 0;
  renderViewControls();
  renderSuggestions(actionState, pool);
  observeLoadMore();
}

function renderBoardRecovery(value: MockState): void {
  if (!value.mock) return;
  const next = value.next ?? null;
  setupPanel.hidden = true;
  activePanel.hidden = false;
  tabs.hidden = true;
  viewTabs.hidden = true;
  columnHead.hidden = true;
  list.hidden = true;
  pickPanel.hidden = false;
  clock.hidden = true;
  suggestions.hidden = true;
  teamCount.textContent = String(value.mock.team_count);
  rounds.textContent = String(value.mock.rounds);
  scoring.textContent = "Unavailable";
  statusSeed.textContent = String(value.mock.seed);
  renderVariancePreset(statusVariance, value.mock.variance_preset);
  statusSlot.textContent = String(value.mock.user_slot);
  statusRound.textContent = next ? String(next.round) : "Done";
  statusOverall.textContent = next ? String(next.overall_pick) : "Done";
  const actionState = actions(false);
  statusLifecycle.textContent = actionState.status_label;
  statusRevision.textContent = String(value.revision);
  events.innerHTML = "<b>Saved board unavailable.</b> Discard this mock to start over.";
  onClock.textContent = "This mock cannot continue with the current tracker version.";
  selected.textContent = "Discard the mock to return to setup.";
  draftPlayer.disabled = true;
  lifecycleToggle.textContent = actionState.lifecycle_label;
  lifecycleToggle.disabled = true;
  undo.disabled = true;
  reset.disabled = true;
  discard.disabled = !actionState.discard_enabled;
  draftError.textContent = value.board_error ?? "The saved board is unavailable.";
  foot.textContent = "Saved board snapshot unavailable";
}

function renderUnavailableSetup(message: string): void {
  setupPanel.hidden = false;
  activePanel.hidden = true;
  tabs.hidden = true;
  viewTabs.hidden = true;
  columnHead.hidden = true;
  list.hidden = true;
  pickPanel.hidden = true;
  teamCount.textContent = "—";
  rounds.textContent = "—";
  scoring.textContent = "Unavailable";
  setupError.textContent = message;
  startButton.disabled = true;
  foot.textContent = "board.json unavailable";
}

function renderCurrentState(): void {
  if (mock?.configured && mock.board_error && mock.mock) {
    renderBoardRecovery(mock);
  } else {
    renderState();
  }
}

function setupBoard(value: Board): void {
  board = value;
  slotInput.replaceChildren(
    ...Array.from({ length: value.num_teams }, (_, index) => {
      const option = document.createElement("option");
      option.value = String(index + 1);
      option.textContent = `Slot ${index + 1}`;
      return option;
    }),
  );
  if (!seedInput.value) seedInput.value = String(generatedSeed());
}

function applyMockState(value: MockState): void {
  const previousMockId = mock?.mock?.id ?? null;
  mock = value;
  playerPool = null;
  pooledPlayers = null;
  pooledPicks = null;
  if (value.configured && value.board && isValidBoard(value.board)) {
    setupBoard(value.board);
    boardView = reconcileMockBoardView(
      boardView,
      previousMockId,
      value.mock?.id ?? null,
      currentPlayerPool().available,
    );
  } else {
    boardView = initialBoardView;
  }
}

async function load(): Promise<boolean> {
  const current = await api<MockState>("/api/mocks/current");
  if (current.response?.status === 401) {
    return false;
  }
  if (
    current.response?.ok
    && current.value?.configured
    && current.value.mock
    && current.value.board_error
  ) {
    applyMockState(current.value);
    startButton.disabled = true;
    setLocked(false);
    renderBoardRecovery(current.value);
    return false;
  }
  if (
    current.response?.ok
    && current.value?.configured
    && current.value.board
    && isValidBoard(current.value.board)
  ) {
    applyMockState(current.value);
    startButton.disabled = false;
    setLocked(false);
    renderState();
    return true;
  }
  if (!current.response?.ok || !current.value) mock = null;
  const boardResult = await api<unknown>("/api/board");
  if (boardResult.response?.status === 401) {
    return false;
  }
  if (!boardResult.response?.ok || !isValidBoard(boardResult.value)) {
    if (current.response?.ok && current.value) {
      applyMockState(current.value);
    }
    const message = "Board unavailable. Publish a supported board and try again.";
    if (board && mock?.configured !== true) {
      renderState();
      setupError.textContent = message;
    } else if (mock?.configured !== true) {
      renderUnavailableSetup(message);
    }
    startButton.disabled = true;
    setLocked(false);
    list.innerHTML =
      '<div class="notice"><b>Board unavailable.</b>Publish a supported board before starting a mock.</div>';
    return false;
  }
  const reconciled = current.response?.ok === true && current.value !== null;
  setupError.textContent = reconciled
    ? ""
    : mockErrorMessage(
      current.value,
      current.transportError ?? "Unable to reconcile the current mock. Reload and try again.",
    );
  setupBoard(boardResult.value);
  if (reconciled) mock = current.value;
  startButton.disabled = !reconciled;
  setLocked(false);
  renderState();
  return reconciled;
}

unlockForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const key = keyInput.value.trim();
  if (!key) {
    setLocked(true, "Enter your API key to continue.");
    return;
  }
  keyStore.set(key);
  await load();
});

startForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (writing) return;
  writing = true;
  startButton.disabled = true;
  setupError.textContent = "";
  const result = await api<MockState>("/api/mocks", {
    method: "POST",
    body: JSON.stringify(readMockSetupControls(slotInput, seedInput, varianceInput)),
  });
  writing = false;
  startButton.disabled = false;
  if (!result.response?.ok || !result.value) {
    renderMockError(
      setupError,
      result.value,
      result.transportError ?? "The mock was not changed.",
    );
    if (result.response?.status === 409) await load();
    return;
  }
  applyMockState(result.value);
  boardView = initialBoardView;
  renderState();
});

tabs.addEventListener("click", (event) => {
  const button = (event.target as Element).closest<HTMLButtonElement>("[data-position]");
  if (!button) return;
  boardView = nextBoardView(boardView, {
    type: "selectPosition",
    position: (button.dataset.position ?? "ALL") as BoardPosition,
  });
  renderState();
});

viewTabs.addEventListener("click", (event) => {
  const button = (event.target as Element).closest<HTMLButtonElement>("[data-view]");
  if (!button || boardView.searchQuery.trim()) return;
  boardView = nextBoardView(boardView, {
    type: "selectMode",
    mode: button.dataset.view === "drafted" ? "drafted" : "available",
  });
  renderState();
});

playerSearch.addEventListener("input", () => {
  boardView = nextBoardView(boardView, { type: "searchChanged", query: playerSearch.value });
  renderState();
});

list.addEventListener("click", (event) => {
  if ((event.target as Element).closest("[data-load-more]")) {
    loadMoreRows();
    return;
  }
  const row = (event.target as Element).closest<HTMLButtonElement>("[data-player-key]");
  if (!row || !actions().can_pick) return;
  boardView = nextBoardView(boardView, {
    type: "playerSelected",
    key: decodeURIComponent(row.dataset.playerKey ?? ""),
  });
  renderState();
});

suggestionList.addEventListener("click", (event) => {
  const button = (event.target as Element).closest<HTMLButtonElement>("[data-player-key]");
  if (!button || !actions().can_pick) return;
  boardView = nextBoardView(boardView, {
    type: "playerSelected",
    key: decodeURIComponent(button.dataset.playerKey ?? ""),
  });
  renderState();
});

pickToolsToggle.addEventListener("click", () => {
  boardView = nextBoardView(boardView, { type: "togglePickTools" });
  renderState();
});

clearSelection.addEventListener("click", () => {
  boardView = nextBoardView(boardView, { type: "selectionCleared" });
  renderState();
});

draftPlayer.addEventListener("click", async () => {
  if (!boardView.selectedKey || !mock?.mock) return;
  const submittedKey = boardView.selectedKey;
  writing = true;
  draftError.textContent = "";
  renderState();
  const result = await api<MockState>("/api/mocks/current/picks", {
    method: "POST",
    body: JSON.stringify({
      mock_id: mock.mock.id,
      player_key: submittedKey,
      expected_revision: mock.revision,
    }),
  });
  writing = false;
  if (!result.response?.ok || !result.value) {
    renderMockError(
      draftError,
      result.value,
      result.transportError ?? "The mock was not changed.",
    );
    if (result.response?.status === 409) {
      boardView = nextBoardView(boardView, { type: "selectionCleared" });
      await load();
    }
    else renderState();
    return;
  }
  applyMockState(result.value);
  boardView = nextBoardView(boardView, { type: "pickRecorded" });
  transitionStatus.textContent = `${result.value.appended_picks?.length ?? 0} picks recorded. ${result.value.next ? `${result.value.next.team_name} is on the clock.` : "Draft complete."}`;
  renderState();
});

async function lifecycleRequest(
  path: string,
  method: "POST" | "DELETE",
  resetView: boolean,
): Promise<void> {
  if (!mock?.mock || writing) return;
  const target = {
    mock_id: mock.mock.id,
    expected_revision: mock.revision,
  };
  writing = true;
  draftError.textContent = "";
  renderCurrentState();
  const result = await api<MockState>(path, {
    method,
    body: JSON.stringify(target),
  });
  writing = false;
  if (!result.response?.ok || !result.value) {
    const message = mockErrorMessage(
      result.value,
      result.transportError ?? "The mock was not changed.",
    );
    if (result.response?.status === 409) {
      boardView = nextBoardView(boardView, { type: "selectionCleared" });
      await load();
      draftError.textContent = message;
    } else {
      renderCurrentState();
      draftError.textContent = message;
    }
    return;
  }
  applyMockState(result.value);
  boardView = resetView
    ? initialBoardView
    : nextBoardView(boardView, { type: "selectionCleared" });
  renderCurrentState();
}

lifecycleToggle.addEventListener("click", async () => {
  const path = mock?.lifecycle === "paused"
    ? "/api/mocks/current/resume"
    : "/api/mocks/current/pause";
  await lifecycleRequest(path, "POST", false);
});

undo.addEventListener("click", async () => {
  await lifecycleRequest("/api/mocks/current/picks/latest", "DELETE", false);
});

reset.addEventListener("click", async () => {
  if (!confirm(
    "Restart this mock from its saved seed? The board snapshot and configuration stay the same, and the seeded opening will be replayed.",
  )) return;
  await lifecycleRequest("/api/mocks/current/reset", "POST", true);
});

discard.addEventListener("click", async () => {
  if (!mock?.mock) return;
  if (!confirm(
    "Discard this mock draft and return to setup? Its saved session will be removed; your live draft will not be changed.",
  )) return;
  writing = true;
  renderCurrentState();
  const result = await api<MockState>("/api/mocks/current", {
    method: "DELETE",
    body: JSON.stringify({
      mock_id: mock.mock.id,
      expected_revision: mock.revision,
    }),
  });
  if (!result.response?.ok || !result.value) {
    writing = false;
    const message = mockErrorMessage(
      result.value,
      result.transportError ?? "The mock was not changed.",
    );
    if (result.response?.status === 409) await load();
    else {
      renderCurrentState();
      draftError.textContent = message;
    }
    return;
  }
  applyMockState(result.value);
  boardView = initialBoardView;
  seedInput.value = String(generatedSeed());
  if (result.value.configured) {
    writing = false;
    renderCurrentState();
  } else {
    startButton.disabled = true;
    renderState();
    await load();
    writing = false;
    renderState();
  }
});

if (keyStore.get()) {
  void load();
} else {
  setLocked(true);
}
