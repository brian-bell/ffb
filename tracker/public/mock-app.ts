import { isValidBoard } from "../src/board";
import type { MockState } from "../src/mock-draft";
import { buildPlayerPool } from "../src/player-pool";
import { renderBoard } from "../src/render";
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
let position = "ALL";
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

async function api<T>(path: string, init: RequestInit = {}): Promise<{
  response: Response;
  value: T | null;
}> {
  const response = await fetch(path, {
    ...init,
    headers: {
      ...authHeaders(init.body !== undefined),
      ...init.headers,
    },
  });
  let value: T | null = null;
  try {
    value = (await response.json()) as T;
  } catch {
    value = null;
  }
  return { response, value };
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
        `<button class="tab" type="button" data-position="${escaped(value)}" aria-selected="${value === position}">${escaped(value)}</button>`,
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
  list.innerHTML = renderBoard(board, position, {
    picked: pool.picked,
    selectable: next?.is_user === true && !writing,
    selectedKey,
    window: { limit: 200 },
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

async function load(): Promise<void> {
  const boardResult = await api<unknown>("/api/board");
  if (boardResult.response.status === 401) {
    keyStore.del();
    setLocked(true, "Invalid API key. Check it and try again.");
    return;
  }
  if (!boardResult.response.ok || !isValidBoard(boardResult.value)) {
    setLocked(false);
    list.innerHTML =
      '<div class="notice"><b>Board unavailable.</b>Publish a supported board before starting a mock.</div>';
    return;
  }
  setupBoard(boardResult.value);
  const current = await api<MockState>("/api/mocks/current");
  if (current.response.status === 401) {
    keyStore.del();
    setLocked(true, "Invalid API key. Check it and try again.");
    return;
  }
  mock = current.value;
  setLocked(false);
  renderState();
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
  if (!result.response.ok || !result.value) {
    setupError.textContent = errorMessage(result.value, "The mock was not changed.");
    if (result.response.status === 409) await load();
    return;
  }
  mock = result.value;
  selectedKey = null;
  renderState();
});

tabs.addEventListener("click", (event) => {
  const button = (event.target as Element).closest<HTMLButtonElement>("[data-position]");
  if (!button) return;
  position = button.dataset.position ?? "ALL";
  selectedKey = null;
  renderState();
});

list.addEventListener("click", (event) => {
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
  if (!result.response.ok || !result.value) {
    draftError.textContent = errorMessage(result.value, "The mock was not changed.");
    if (result.response.status === 409) await load();
    else renderState();
    return;
  }
  mock = result.value;
  selectedKey = null;
  renderState();
});

discard.addEventListener("click", async () => {
  if (!confirm("Discard this mock draft? Your live draft will not be changed.")) return;
  writing = true;
  renderState();
  const result = await api<MockState>("/api/mocks/current", { method: "DELETE" });
  writing = false;
  if (!result.response.ok || !result.value) {
    draftError.textContent = errorMessage(result.value, "The mock was not changed.");
    renderState();
    return;
  }
  mock = result.value;
  selectedKey = null;
  seedInput.value = String(generatedSeed());
  renderState();
});

if (keyStore.get()) {
  void load();
} else {
  setLocked(true);
}
