import { nextPick, type DraftTeam, type NextPick } from "./draft";
import { playersEquivalent } from "./player-identity";

export interface TeamInput {
  name: string;
  is_user: boolean;
}

export interface DraftConfigInput {
  name: string;
  rounds: number;
  teams: TeamInput[];
}

export interface DraftInfo {
  name: string;
  rounds: number;
  team_count: number;
}

export interface RecordedPick {
  overall_pick: number;
  round: number;
  round_pick: number;
  team_id: number;
  team_name: string;
  player_key: string;
  player_name: string;
  player_pos: string | null;
  player_team: string | null;
  picked_at: string;
}

export interface DraftState {
  configured: boolean;
  draft?: DraftInfo;
  teams?: DraftTeam[];
  picks: RecordedPick[];
  next?: NextPick | null;
  complete?: boolean;
  revision: number;
}

export interface PickPlayer {
  key: string;
  name: string;
  pos: string | null;
  team: string | null;
}

export type RecordResult = "ok" | "draft_unconfigured" | "draft_complete" | "stale_draft" | "player_already_picked";
export type UndoResult = "ok" | "no_picks" | "stale_draft";

interface DraftRow {
  name: string;
  rounds: number;
}

interface TeamRow {
  id: number;
  name: string;
  draft_slot: number;
  is_user: number;
}

interface PickRow extends RecordedPick {}

function isoNow(): string {
  return new Date().toISOString();
}

function asTeams(rows: TeamRow[]): DraftTeam[] {
  return rows.map((team) => ({ ...team, is_user: team.is_user === 1 }));
}

export async function getDraftState(db: D1Database): Promise<DraftState> {
  const draft = await db.prepare("SELECT name, rounds FROM drafts WHERE id = 1").first<DraftRow>();
  if (!draft) return { configured: false, picks: [], revision: 0 };

  const teamRows = await db
    .prepare("SELECT id, name, draft_slot, is_user FROM teams WHERE draft_id = 1 ORDER BY draft_slot")
    .all<TeamRow>();
  const teams = asTeams(teamRows.results);
  const pickRows = await db
    .prepare(
      `SELECT p.overall_pick, p.round, p.round_pick, p.team_id, t.name AS team_name,
              p.player_key, p.player_name, p.player_pos, p.player_team, p.picked_at
         FROM picks p JOIN teams t ON t.id = p.team_id
        WHERE p.draft_id = 1 ORDER BY p.overall_pick`,
    )
    .all<PickRow>();
  const picks = pickRows.results;
  const next = nextPick(teams, draft.rounds, picks.length + 1);
  return {
    configured: true,
    draft: { name: draft.name, rounds: draft.rounds, team_count: teams.length },
    teams,
    picks,
    next,
    complete: next === null,
    revision: picks.length,
  };
}

/** Replace only an unstarted draft configuration; callers validate user input. */
export async function configureDraft(db: D1Database, config: DraftConfigInput): Promise<DraftState> {
  const existing = await db.prepare("SELECT COUNT(*) AS count FROM picks WHERE draft_id = 1").first<{ count: number }>();
  if ((existing?.count ?? 0) > 0) throw new Error("draft_started");
  const now = isoNow();
  await db.batch([
    db.prepare("DELETE FROM teams WHERE draft_id = 1"),
    db.prepare("DELETE FROM drafts WHERE id = 1"),
    db.prepare("INSERT INTO drafts (id, name, rounds, created_at, updated_at) VALUES (1, ?, ?, ?, ?)").bind(config.name, config.rounds, now, now),
    ...config.teams.map((team, draftSlot) =>
      db
        .prepare("INSERT INTO teams (draft_id, name, draft_slot, is_user) VALUES (1, ?, ?, ?)")
        .bind(team.name, draftSlot, team.is_user ? 1 : 0),
    ),
  ]);
  return getDraftState(db);
}

/** Append one server-derived pick. Schema uniqueness constraints are the race guard. */
export async function recordPick(db: D1Database, player: PickPlayer, expectedOverallPick: number): Promise<RecordResult> {
  const state = await getDraftState(db);
  if (!state.configured) return "draft_unconfigured";
  if (!state.next) return "draft_complete";
  if (state.next.overall_pick !== expectedOverallPick) return "stale_draft";
  if (state.picks.some((picked) => playersEquivalent(
    { key: player.key, name: player.name, pos: player.pos, team: player.team },
    { key: picked.player_key, name: picked.player_name, pos: picked.player_pos, team: picked.player_team },
  ))) return "player_already_picked";
  try {
    await db
      .prepare(
        `INSERT INTO picks
          (draft_id, overall_pick, round, round_pick, team_id, player_key, player_name, player_pos, player_team, picked_at)
         VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      )
      .bind(
        state.next.overall_pick,
        state.next.round,
        state.next.round_pick,
        state.next.team_id,
        player.key,
        player.name,
        player.pos,
        player.team,
        isoNow(),
      )
      .run();
  } catch {
    return "stale_draft";
  }
  return "ok";
}

/** Delete exactly the row that was latest when the UI rendered it. */
export async function undoLatestPick(db: D1Database, expectedOverallPick: number): Promise<UndoResult> {
  const latest = await db.prepare("SELECT MAX(overall_pick) AS overall_pick FROM picks WHERE draft_id = 1").first<{ overall_pick: number | null }>();
  if (latest?.overall_pick == null) return "no_picks";
  const result = await db
    .prepare(
      `DELETE FROM picks
        WHERE draft_id = 1 AND overall_pick = ?
          AND overall_pick = (SELECT MAX(overall_pick) FROM picks WHERE draft_id = 1)`,
    )
    .bind(expectedOverallPick)
    .run();
  return result.meta.changes === 1 ? "ok" : "stale_draft";
}

/** Explicit ordered reset: never rely on SQLite foreign-key cascade settings. */
export async function resetDraft(db: D1Database): Promise<void> {
  await db.batch([
    db.prepare("DELETE FROM picks WHERE draft_id = 1"),
    db.prepare("DELETE FROM teams WHERE draft_id = 1"),
    db.prepare("DELETE FROM drafts WHERE id = 1"),
  ]);
}
