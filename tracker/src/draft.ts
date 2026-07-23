export interface DraftTeam {
  id: number;
  name: string;
  draft_slot: number;
  is_user: boolean;
}

export interface NextPick {
  overall_pick: number;
  round: number;
  round_pick: number;
  team_id: number;
  team_name: string;
  is_user: boolean;
  direction: "forward" | "reverse";
}

function shortTeamName(name: string): string {
  const characters = Array.from(name);
  return characters.length <= 9 ? name : `${characters.slice(0, 6).join("")}...`;
}

export function draftClockLabel(next: Pick<NextPick, "round" | "round_pick" | "team_name">): string {
  return `Rd ${next.round} P${next.round_pick} · ${shortTeamName(next.team_name)}`;
}

export function draftNextLabel(teamName: string | null): string {
  return teamName === null ? "Next: Done" : `Next: ${shortTeamName(teamName)}`;
}

export interface DraftClockPresentation {
  current: string;
  next: string;
  accessible: string;
}

/** Visual clock labels plus an unabridged summary for assistive technology. */
export function draftClockPresentation(
  currentPick: Pick<NextPick, "round" | "round_pick" | "team_name">,
  nextTeamName: string | null,
): DraftClockPresentation {
  return {
    current: draftClockLabel(currentPick),
    next: draftNextLabel(nextTeamName),
    accessible: `Round ${currentPick.round}, pick ${currentPick.round_pick}. ${currentPick.team_name}. Next: ${nextTeamName ?? "Draft complete"}`,
  };
}

export function draftPageTitle(draftName: string | null, inProgress: boolean): string {
  return inProgress && draftName ? draftName : "Draft Room";
}

/** Derive the on-clock team for a one-based overall pick in a snake draft. */
export function nextPick(teams: DraftTeam[], rounds: number, overallPick: number): NextPick | null {
  if (!Number.isInteger(overallPick) || overallPick < 1 || overallPick > teams.length * rounds) return null;
  const round = Math.floor((overallPick - 1) / teams.length) + 1;
  const offset = (overallPick - 1) % teams.length;
  const direction = round % 2 === 1 ? "forward" : "reverse";
  const slot = direction === "forward" ? offset : teams.length - 1 - offset;
  const team = teams.find((candidate) => candidate.draft_slot === slot);
  if (!team) return null;
  return {
    overall_pick: overallPick,
    round,
    round_pick: offset + 1,
    team_id: team.id,
    team_name: team.name,
    is_user: team.is_user,
    direction,
  };
}
