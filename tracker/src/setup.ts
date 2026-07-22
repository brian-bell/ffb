import type { TeamInput } from "./draft-store";

/** Translate the setup form's first-round order into the API's team payload. */
export function teamsFromSetup(namesText: string, userTeamName: string): TeamInput[] {
  return namesText
    .split("\n")
    .map((name) => name.trim())
    .filter(Boolean)
    .map((name) => ({ name, is_user: name === userTeamName }));
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
