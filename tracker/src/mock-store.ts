import { nextPick, type DraftTeam } from "./draft";
import type {
  MockAggregate,
  MockInfo,
  MockPick,
  MockState,
  MockTransition,
} from "./mock-draft";
import type { Board } from "./types";

export interface MockBoardSnapshot {
  id: string;
  fingerprint: string;
  board_json: string;
  board: Board;
}

export interface LoadedMock {
  board_json: string;
  aggregate: MockAggregate;
  state: MockState;
}

interface DraftRow {
  id: string;
  board_fingerprint: string;
  board_json: string;
  status: "active" | "complete";
  seed: number;
  rng_state: number;
  strategy_version: string;
  user_slot: number;
  team_count: number;
  rounds: number;
  revision: number;
}

interface TeamRow {
  draft_slot: number;
  name: string;
  is_user: number;
}

interface PickRow extends MockPick {}

function isoNow(): string {
  return new Date().toISOString();
}

function mockInfo(row: DraftRow): MockInfo {
  return {
    id: row.id,
    board_fingerprint: row.board_fingerprint,
    seed: row.seed,
    strategy_version: row.strategy_version,
    user_slot: row.user_slot,
    team_count: row.team_count,
    rounds: row.rounds,
  };
}

function publicState(row: DraftRow, aggregate: MockAggregate): MockState {
  return {
    configured: true,
    mock: mockInfo(row),
    teams: aggregate.teams,
    picks: aggregate.picks,
    next: aggregate.next,
    complete: aggregate.complete,
    revision: aggregate.revision,
  };
}

async function hydrateMock(db: D1Database, row: DraftRow): Promise<LoadedMock> {
  const teamRows = await db
    .prepare(
      `SELECT draft_slot, name, is_user
         FROM mock_teams
        WHERE mock_id = ?
        ORDER BY draft_slot`,
    )
    .bind(row.id)
    .all<TeamRow>();
  const teams: DraftTeam[] = teamRows.results.map((team) => ({
    id: team.draft_slot + 1,
    name: team.name,
    draft_slot: team.draft_slot,
    is_user: team.is_user === 1,
  }));
  const pickRows = await db
    .prepare(
      `SELECT p.overall_pick, p.round, p.round_pick, t.name AS team_name,
              p.draft_slot, p.player_key, p.player_name, p.player_pos,
              p.player_team, p.source
         FROM mock_picks p
         JOIN mock_teams t
           ON t.mock_id = p.mock_id AND t.draft_slot = p.draft_slot
        WHERE p.mock_id = ?
        ORDER BY p.overall_pick`,
    )
    .bind(row.id)
    .all<PickRow>();
  const picks = pickRows.results;
  const next = nextPick(teams, row.rounds, picks.length + 1);
  const aggregate: MockAggregate = {
    seed: row.seed,
    strategy_version: row.strategy_version,
    user_slot: row.user_slot,
    team_count: row.team_count,
    rounds: row.rounds,
    teams,
    picks,
    next,
    complete: row.status === "complete" || next === null,
    revision: row.revision,
    rng_state: row.rng_state,
  };
  return {
    board_json: row.board_json,
    aggregate,
    state: publicState(row, aggregate),
  };
}

const MOCK_ROW_SELECT = `SELECT d.id, d.board_fingerprint, b.board_json, d.status, d.seed,
                                d.rng_state, d.strategy_version, d.user_slot, d.team_count,
                                d.rounds, d.revision
                           FROM mock_drafts d
                           JOIN mock_boards b ON b.fingerprint = d.board_fingerprint`;

export async function loadMock(
  db: D1Database,
  mockId: string,
): Promise<LoadedMock | null> {
  const row = await db
    .prepare(`${MOCK_ROW_SELECT} WHERE d.id = ?`)
    .bind(mockId)
    .first<DraftRow>();
  return row ? hydrateMock(db, row) : null;
}

export async function loadCurrentMock(db: D1Database): Promise<LoadedMock | null> {
  const row = await db
    .prepare(
      `${MOCK_ROW_SELECT}
        ORDER BY CASE WHEN d.status = 'active' THEN 0 ELSE 1 END, d.updated_at DESC
        LIMIT 1`,
    )
    .first<DraftRow>();
  return row ? hydrateMock(db, row) : null;
}

export async function hasActiveMock(db: D1Database): Promise<boolean> {
  const row = await db
    .prepare("SELECT 1 AS present FROM mock_drafts WHERE status = 'active' LIMIT 1")
    .first<{ present: number }>();
  return row !== null;
}

export async function insertMock(
  db: D1Database,
  aggregate: MockAggregate,
  snapshot: MockBoardSnapshot,
): Promise<void> {
  const now = isoNow();
  await db.batch([
    db.prepare(
      `INSERT INTO mock_boards
        (fingerprint, board_json, board_version, season, generated_at, created_at)
       VALUES (?, ?, ?, ?, ?, ?)
       ON CONFLICT(fingerprint) DO NOTHING`,
    ).bind(
      snapshot.fingerprint,
      snapshot.board_json,
      snapshot.board.version,
      snapshot.board.season,
      snapshot.board.generated_at,
      now,
    ),
    db.prepare(
      `INSERT INTO mock_drafts
        (id, board_fingerprint, status, seed, rng_state, strategy_version,
         user_slot, team_count, rounds, revision, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    ).bind(
      snapshot.id,
      snapshot.fingerprint,
      aggregate.complete ? "complete" : "active",
      aggregate.seed,
      aggregate.rng_state,
      aggregate.strategy_version,
      aggregate.user_slot,
      aggregate.team_count,
      aggregate.rounds,
      aggregate.revision,
      now,
      now,
    ),
    ...aggregate.teams.map((team) =>
      db.prepare(
        `INSERT INTO mock_teams (mock_id, draft_slot, name, is_user)
         VALUES (?, ?, ?, ?)`,
      ).bind(snapshot.id, team.draft_slot, team.name, team.is_user ? 1 : 0),
    ),
    ...aggregate.picks.map((pick) =>
      db.prepare(
        `INSERT INTO mock_picks
          (mock_id, overall_pick, round, round_pick, draft_slot, player_key,
           player_name, player_pos, player_team, source, picked_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      ).bind(
        snapshot.id,
        pick.overall_pick,
        pick.round,
        pick.round_pick,
        pick.draft_slot,
        pick.player_key,
        pick.player_name,
        pick.player_pos,
        pick.player_team,
        pick.source,
        now,
      ),
    ),
  ]);
}

export type AppendMockResult = "ok" | "stale_mock";

export async function appendMockTransition(
  db: D1Database,
  mockId: string,
  expectedRevision: number,
  transition: MockTransition,
): Promise<AppendMockResult> {
  const now = isoNow();
  const statements = transition.appended_picks.map((pick) =>
    db.prepare(
      `INSERT INTO mock_picks
        (mock_id, overall_pick, round, round_pick, draft_slot, player_key,
         player_name, player_pos, player_team, source, picked_at)
       SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        WHERE EXISTS (
          SELECT 1 FROM mock_drafts
           WHERE id = ? AND revision = ? AND status = 'active'
        )`,
    ).bind(
      mockId,
      pick.overall_pick,
      pick.round,
      pick.round_pick,
      pick.draft_slot,
      pick.player_key,
      pick.player_name,
      pick.player_pos,
      pick.player_team,
      pick.source,
      now,
      mockId,
      expectedRevision,
    ),
  );
  const results = await db.batch([
    ...statements,
    db.prepare(
      `UPDATE mock_drafts
          SET rng_state = ?, revision = ?, status = ?, updated_at = ?
        WHERE id = ? AND revision = ? AND status = 'active'`,
    ).bind(
      transition.next_rng_state,
      expectedRevision + transition.appended_picks.length,
      transition.complete ? "complete" : "active",
      now,
      mockId,
      expectedRevision,
    ),
  ]);
  return results.at(-1)?.meta.changes === 1 ? "ok" : "stale_mock";
}

export type DiscardMockResult = "ok" | "stale_mock";

export async function discardCurrentMock(
  db: D1Database,
  mockId: string,
): Promise<DiscardMockResult> {
  const current = await db
    .prepare(
      `SELECT id FROM mock_drafts
        ORDER BY CASE WHEN status = 'active' THEN 0 ELSE 1 END, updated_at DESC
        LIMIT 1`,
    )
    .first<{ id: string }>();
  if (current?.id !== mockId) return "stale_mock";
  const results = await db.batch([
    db.prepare("DELETE FROM mock_picks WHERE mock_id = ?").bind(mockId),
    db.prepare("DELETE FROM mock_teams WHERE mock_id = ?").bind(mockId),
    db.prepare("DELETE FROM mock_drafts WHERE id = ?").bind(mockId),
  ]);
  return results.at(-1)?.meta.changes === 1 ? "ok" : "stale_mock";
}

export function unconfiguredMockState(): MockState {
  return { configured: false, picks: [], revision: 0 };
}
