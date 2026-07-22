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
