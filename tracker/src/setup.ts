import type { DraftConfigInput, TeamInput } from "./draft-store";

interface SetupStorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export interface SetupStore {
  get(): DraftConfigInput | null;
  set(config: DraftConfigInput): void;
}

const SETUP_STORAGE_KEY = "ffb.lastDraftSetup";

function parseStoredSetup(raw: string | null): DraftConfigInput | null {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<DraftConfigInput>;
    if (typeof value.name !== "string" || typeof value.rounds !== "number" || !Array.isArray(value.teams)) return null;
    if (!value.teams.every((team) => typeof team?.name === "string" && typeof team?.is_user === "boolean")) return null;
    if (value.teams.filter((team) => team.is_user).length !== 1) return null;
    if (setupValidation(value.teams.map((team) => team.name).join("\n"), value.teams.find((team) => team.is_user)?.name ?? "", value.rounds) !== null) return null;
    return { name: value.name, rounds: value.rounds, teams: value.teams };
  } catch {
    return null;
  }
}

/** Browser-local memory of the most recently successful draft configuration. */
export function makeSetupStore(storage: SetupStorageLike | null): SetupStore {
  let memory: DraftConfigInput | null = null;
  return {
    get() {
      try {
        return parseStoredSetup(storage?.getItem(SETUP_STORAGE_KEY) ?? null) ?? memory;
      } catch {
        return memory;
      }
    },
    set(config) {
      memory = config;
      try {
        storage?.setItem(SETUP_STORAGE_KEY, JSON.stringify(config));
      } catch {
        /* memory already holds the setup */
      }
    },
  };
}

export type SetupDialogEvent = { type: "open" } | { type: "close" } | { type: "draftReset" };

/** Keep setup presentation explicit instead of deriving it from draft configuration. */
export function nextSetupDialog(_open: boolean, event: SetupDialogEvent): boolean {
  return event.type === "open";
}

/** Translate the setup form's first-round order into the API's team payload. */
export function teamsFromSetup(namesText: string, userTeamName: string): TeamInput[] {
  return namesText
    .split("\n")
    .map((name) => name.trim())
    .filter(Boolean)
    .map((name) => ({ name, is_user: name === userTeamName }));
}

/** Replace the user-team choices without parsing team names as HTML. */
export function replaceTeamOptions(select: HTMLSelectElement, names: string[]): void {
  const selectedName = select.value;
  const options = names.map((name) => {
    const option = select.ownerDocument.createElement("option");
    option.value = name;
    option.textContent = name;
    return option;
  });
  select.replaceChildren(...options);
  if (names.includes(selectedName)) select.value = selectedName;
}

/** Local feedback for the few setup errors we can explain before a network call. */
export function validateSetup(namesText: string, userTeamName: string, rounds = 16): string | null {
  return setupValidation(namesText, userTeamName, rounds)?.message ?? null;
}

export interface SetupValidationError {
  field: "rounds" | "teams" | "user_team";
  message: string;
}

/** The message and field for local setup validation, or null when ready to save. */
export function setupValidation(namesText: string, userTeamName: string, rounds = 16): SetupValidationError | null {
  const teams = teamsFromSetup(namesText, userTeamName);
  if (!Number.isInteger(rounds) || rounds < 1 || rounds > 30) return { field: "rounds", message: "Choose between 1 and 30 rounds." };
  if (teams.length < 2) return { field: "teams", message: "Add at least two teams to start a draft." };
  if (!userTeamName) return { field: "user_team", message: "Choose Brian’s team below." };
  if (new Set(teams.map((team) => team.name.toLocaleLowerCase())).size !== teams.length) return { field: "teams", message: "Team names must be unique." };
  if (!teams.some((team) => team.is_user)) return { field: "user_team", message: "Choose Brian’s team from the list." };
  return null;
}
