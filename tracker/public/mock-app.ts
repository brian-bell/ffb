import { isValidBoard } from "../src/board";
import type { MockState } from "../src/mock-draft";
import { initialMockView, nextMockView } from "../src/mock-view";
import { buildPlayerPool } from "../src/player-pool";
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
const startButton = element<HTMLButtonElement>("[data-start]");
const setupError = element<HTMLElement>("[data-error]");
const tabs = element<HTMLElement>("[data-tabs]");
const columnHead = element<HTMLElement>("[data-colhead]");
const list = element<HTMLElement>("[data-list]");
const pickPanel = element<HTMLElement>("[data-pick-panel]");
const onClock = element<HTMLElement>("[data-on-clock]");
const selected = element<HTMLElement>("[data-selected]");
const draftPlayer = element<HTMLButtonElement>("[data-draft-player]");
const discard = element<HTMLButtonElement>("[data-discard]");
const draftError = element<HTMLElement>("[data-draft-error]");
const events = element<HTMLElement>("[data-events]");
const foot = element<HTMLElement>("[data-foot]");
const teamCount = element<HTMLElement>("[data-team-count]");
const rounds = element<HTMLElement>("[data-rounds]");
const scoring = element<HTMLElement>("[data-scoring]");
const statusSeed = element<HTMLElement>("[data-status-seed]");
const statusSlot = element<HTMLElement>("[data-status-slot]");
const statusRound = element<HTMLElement>("[data-status-round]");
const statusOverall = element<HTMLElement>("[data-status-overall]");
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
let selectedKey: string | null = null;
let mockView = initialMockView;
let writing = false;

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
  return requestJson<T>(fetch, path, {
    ...init,
    headers: {
      ...authHeaders(init.body !== undefined),
      ...init.headers,
    },
  });
}

function availablePool() {
  if (!board) throw new Error("board unavailable");
  return buildPlayerPool(board.players, mock?.picks ?? []);
}

function selectedPlayer(): Player | null {
  return board?.players.find((player) => player.key === selectedKey) ?? null;
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
        `<button class="tab" type="button" data-position="${escaped(value)}" aria-selected="${value === mockView.position}">${escaped(value)}</button>`,
    )
    .join("");
}

function renderEvents(): void {
  const latest = mock?.appended_picks ?? [];
  if (latest.length === 0) {
    events.innerHTML = "<span>No CPU picks in the latest transition.</span>";
    return;
  }
  const simulated = latest.filter((pick) => pick.source === "simulated");
  events.innerHTML = simulated.length
    ? `<b>${simulated.length} CPU pick${simulated.length === 1 ? "" : "s"}</b> ${simulated
        .slice(-4)
        .map((pick) => `${pick.round}.${String(pick.round_pick).padStart(2, "0")} ${escaped(pick.player_name)}`)
        .join(" · ")}`
    : "<b>Your pick was recorded.</b>";
}

function renderState(): void {
  if (!board) return;
  const configured = mock?.configured === true && mock.mock !== undefined;
  setupPanel.hidden = configured;
  activePanel.hidden = !configured;
  tabs.hidden = !configured;
  columnHead.hidden = !configured;
  list.hidden = !configured;
  pickPanel.hidden = !configured;

  teamCount.textContent = String(board.num_teams);
  rounds.textContent = String(boardRounds(board));
  scoring.textContent = board.scoring;
  foot.textContent = `board.json v${board.version} · ${board.generated_at}`;
  if (!configured || !mock?.mock) {
    list.innerHTML =
      '<div class="notice"><b>Ready for an isolated rehearsal.</b>Choose a slot and seed to begin.</div>';
    return;
  }

  const next = mock.next ?? null;
  statusSeed.textContent = String(mock.mock.seed);
  statusSlot.textContent = String(mock.mock.user_slot);
  statusRound.textContent = next ? String(next.round) : "Done";
  statusOverall.textContent = next ? String(next.overall_pick) : "Done";
  statusRevision.textContent = String(mock.revision);
  onClock.textContent = next
    ? `${next.team_name} · Round ${next.round}, pick ${next.round_pick}`
    : "Mock complete";
  const player = selectedPlayer();
  selected.innerHTML = player
    ? `<b>${escaped(player.name)}</b> · ${escaped(player.pos ?? "—")} · ${escaped(player.team ?? "FA")}`
    : "No Player Selected.";
  draftPlayer.disabled = writing || !player || next?.is_user !== true;
  discard.disabled = writing;
  renderEvents();
  renderTabs();

  const pool = availablePool();
  list.innerHTML = renderBoard(board, mockView.position, {
    picked: pool.picked,
    selectable: next?.is_user === true && !writing,
    selectedKey,
    window: { limit: mockView.visibleLimit },
  });
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

function errorMessage(value: unknown, fallback: string): string {
  if (value && typeof value === "object" && "message" in value) {
    const message = (value as { message?: unknown }).message;
    if (typeof message === "string") return message;
  }
  return fallback;
}

function applyMockState(value: MockState): void {
  mock = value;
  if (value.configured && value.board && isValidBoard(value.board)) {
    setupBoard(value.board);
  }
}

async function load(): Promise<boolean> {
  const current = await api<MockState>("/api/mocks/current");
  if (current.response?.status === 401) {
    keyStore.del();
    setLocked(true, "Invalid API key. Check it and try again.");
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
    keyStore.del();
    setLocked(true, "Invalid API key. Check it and try again.");
    return false;
  }
  if (!boardResult.response?.ok || !isValidBoard(boardResult.value)) {
    if (current.response?.ok && current.value) {
      applyMockState(current.value);
    }
    if (board && mock?.configured !== true) {
      renderState();
      setupError.textContent = "Board unavailable. Publish a supported board and try again.";
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
    : errorMessage(
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
    body: JSON.stringify({
      user_slot: Number(slotInput.value),
      seed: Number(seedInput.value),
    }),
  });
  writing = false;
  startButton.disabled = false;
  if (!result.response?.ok || !result.value) {
    setupError.textContent = errorMessage(
      result.value,
      result.transportError ?? "The mock was not changed.",
    );
    if (result.response?.status === 409) await load();
    return;
  }
  applyMockState(result.value);
  selectedKey = null;
  mockView = initialMockView;
  renderState();
});

tabs.addEventListener("click", (event) => {
  const button = (event.target as Element).closest<HTMLButtonElement>("[data-position]");
  if (!button) return;
  mockView = nextMockView(mockView, {
    type: "selectPosition",
    position: button.dataset.position ?? "ALL",
  });
  selectedKey = null;
  renderState();
});

list.addEventListener("click", (event) => {
  if ((event.target as Element).closest("[data-load-more]")) {
    mockView = nextMockView(mockView, { type: "loadMore" });
    renderState();
    return;
  }
  const row = (event.target as Element).closest<HTMLButtonElement>("[data-player-key]");
  if (!row || writing || mock?.next?.is_user !== true) return;
  selectedKey = decodeURIComponent(row.dataset.playerKey ?? "");
  renderState();
});

draftPlayer.addEventListener("click", async () => {
  if (!selectedKey || !mock) return;
  writing = true;
  draftError.textContent = "";
  renderState();
  const result = await api<MockState>("/api/mocks/current/picks", {
    method: "POST",
    body: JSON.stringify({
      player_key: selectedKey,
      expected_revision: mock.revision,
    }),
  });
  writing = false;
  if (!result.response?.ok || !result.value) {
    draftError.textContent = errorMessage(
      result.value,
      result.transportError ?? "The mock was not changed.",
    );
    if (result.response?.status === 409) await load();
    else renderState();
    return;
  }
  applyMockState(result.value);
  selectedKey = null;
  renderState();
});

discard.addEventListener("click", async () => {
  if (!mock?.mock) return;
  if (!confirm("Discard this mock draft? Your live draft will not be changed.")) return;
  writing = true;
  renderState();
  const result = await api<MockState>("/api/mocks/current", {
    method: "DELETE",
    body: JSON.stringify({ mock_id: mock.mock.id }),
  });
  if (!result.response?.ok || !result.value) {
    writing = false;
    draftError.textContent = errorMessage(
      result.value,
      result.transportError ?? "The mock was not changed.",
    );
    if (result.response?.status === 409) await load();
    else renderState();
    return;
  }
  applyMockState(result.value);
  selectedKey = null;
  mockView = initialMockView;
  seedInput.value = String(generatedSeed());
  if (result.value.configured) {
    writing = false;
    renderState();
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
