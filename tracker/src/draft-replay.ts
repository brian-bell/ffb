import { nextPick } from "./draft";
import type { DraftState } from "./draft-store";

/** A replay is a read-only prefix of saved picks, never a live draft write. */
export function replayState(saved: DraftState, cursor: number): DraftState {
  if (!saved.configured || !saved.draft || !saved.teams
    || !Number.isInteger(cursor) || cursor < 0 || cursor > saved.picks.length) {
    throw new Error("Invalid saved draft or replay position.");
  }
  const next = nextPick(saved.teams, saved.draft.rounds, cursor + 1);
  return { ...saved, picks: saved.picks.slice(0, cursor), next, complete: next === null, revision: cursor };
}

/** Skip forward to just before a future own pick; at an own turn, skip that turn. */
export function nextOwnPickCursor(saved: DraftState, cursor: number): number {
  const user = saved.teams?.find(team => team.is_user);
  const index = saved.picks.findIndex((pick, i) => i > cursor && pick.team_id === user?.id);
  return index < 0 ? saved.picks.length : index;
}

/** Validate only the pick-list contract; a saved board is deliberately ignored. */
export function parseReplayDraft(value: unknown): DraftState {
  if (!value || typeof value !== "object") throw new Error("Choose a saved draft JSON file.");
  const raw = value as DraftState;
  const config = raw.draft;
  const teams = raw.teams;
  const picks = raw.picks;
  if (!raw.configured || !config || typeof config.name !== "string"
    || !Number.isInteger(config.rounds) || config.rounds < 1 || config.rounds > 30
    || !Array.isArray(teams) || teams.length < 2 || teams.length > 20
    || config.team_count !== teams.length
    || teams.some((t, i) => !t || !Number.isInteger(t.id) || t.draft_slot !== i
      || typeof t.name !== "string" || !t.name.trim() || typeof t.is_user !== "boolean")
    || new Set(teams.map(t => t.id)).size !== teams.length
    || teams.filter(t => t.is_user).length !== 1
    || !Array.isArray(picks) || picks.length === 0 || picks.length > teams.length * config.rounds) {
    throw new Error("Saved draft must contain ordered teams, one user team, and recorded picks.");
  }
  const keys = new Set<string>();
  for (const [index, pick] of picks.entries()) {
    const turn = nextPick(teams, config.rounds, index + 1)!;
    if (!pick || pick.overall_pick !== turn.overall_pick || pick.round !== turn.round
      || pick.round_pick !== turn.round_pick || pick.team_id !== turn.team_id
      || pick.team_name !== turn.team_name || typeof pick.player_key !== "string" || !pick.player_key
      || keys.has(pick.player_key) || typeof pick.player_name !== "string" || !pick.player_name
      || !(pick.player_pos === null || typeof pick.player_pos === "string")
      || !(pick.player_team === null || typeof pick.player_team === "string")) {
      throw new Error("Saved picks must be unique and in their original snake order.");
    }
    keys.add(pick.player_key);
  }
  return replayState(structuredClone(raw), picks.length);
}

export interface ReplaySession { saved: DraftState; cursor: number }
export const REPLAY_STORAGE_KEY = "ffb.draftReplay.v1";

export function readReplaySession(storage: Pick<Storage, "getItem"> | null): ReplaySession | null {
  try {
    const text = storage?.getItem(REPLAY_STORAGE_KEY);
    if (!text) return null;
    const value = JSON.parse(text) as ReplaySession;
    const saved = parseReplayDraft(value.saved);
    replayState(saved, value.cursor);
    return { saved, cursor: value.cursor };
  } catch { return null; }
}
