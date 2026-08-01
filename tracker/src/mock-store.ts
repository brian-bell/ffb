import { nextPick, type DraftTeam } from "./draft";
import type {
  MockAggregate,
  MockInfo,
  MockPick,
  MockState,
  MockTransition,
} from "./mock-draft";
import type { Board } from "./types";
import type { VariancePreset } from "./mock-strategy";

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
  paused: number;
  seed: number;
  rng_state: number;
  strategy_version: string;
  user_slot: number;
  team_count: number;
  rounds: number;
  variance_preset: VariancePreset;
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
    variance_preset: row.variance_preset,
  };
}

function publicState(
  row: DraftRow,
  aggregate: MockAggregate,
  canUndo: boolean,
): MockState {
  return {
    configured: true,
    mock: mockInfo(row),
    teams: aggregate.teams,
    picks: aggregate.picks,
    next: aggregate.next,
    complete: aggregate.complete,
    lifecycle: aggregate.complete ? "complete" : row.paused === 1 ? "paused" : "active",
    can_undo: canUndo,
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
  const checkpoint = await db.prepare(
    "SELECT 1 AS present FROM mock_checkpoints WHERE mock_id = ? LIMIT 1",
  ).bind(row.id).first<{ present: number }>();
  const next = nextPick(teams, row.rounds, picks.length + 1);
  const aggregate: MockAggregate = {
    seed: row.seed,
    strategy_version: row.strategy_version,
    user_slot: row.user_slot,
    team_count: row.team_count,
    rounds: row.rounds,
    variance_preset: row.variance_preset,
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
    state: publicState(row, aggregate, checkpoint !== null),
  };
}

const MOCK_ROW_SELECT = `SELECT d.id, d.board_fingerprint, b.board_json, d.status, d.paused, d.seed,
                                d.rng_state, d.strategy_version, d.user_slot, d.team_count,
                                d.rounds, d.variance_preset, d.revision
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
        (id, board_fingerprint, status, paused, seed, rng_state, strategy_version,
         user_slot, team_count, rounds, variance_preset, revision, created_at, updated_at)
       VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
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
      aggregate.variance_preset,
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
           WHERE id = ? AND revision = ? AND status = 'active' AND paused = 0
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
    db.prepare(
      `INSERT INTO mock_checkpoints
        (mock_id, decision_overall_pick, pick_count_before, rng_state_before, created_at)
       SELECT ?, ?, ?, ?, ?
        WHERE EXISTS (
          SELECT 1 FROM mock_drafts
           WHERE id = ? AND revision = ? AND status = 'active' AND paused = 0
        )`,
    ).bind(
      mockId,
      transition.checkpoint.decision_overall_pick,
      transition.checkpoint.pick_count_before,
      transition.checkpoint.rng_state_before,
      now,
      mockId,
      expectedRevision,
    ),
    ...statements,
    db.prepare(
      `UPDATE mock_drafts
          SET rng_state = ?, revision = ?, status = ?, updated_at = ?
        WHERE id = ? AND revision = ? AND status = 'active' AND paused = 0`,
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

export type SetMockPausedResult = "ok" | "stale_mock" | "invalid_state";

export async function setMockPaused(
  db: D1Database,
  mockId: string,
  expectedRevision: number,
  paused: boolean,
): Promise<SetMockPausedResult> {
  const result = await db.prepare(
    `UPDATE mock_drafts
        SET paused = ?, revision = ?, updated_at = ?
      WHERE id = ? AND revision = ? AND status = 'active' AND paused = ?`,
  ).bind(
    paused ? 1 : 0,
    expectedRevision + 1,
    isoNow(),
    mockId,
    expectedRevision,
    paused ? 0 : 1,
  ).run();
  if (result.meta.changes === 1) return "ok";
  const current = await db.prepare(
    "SELECT 1 AS present FROM mock_drafts WHERE id = ? AND revision = ?",
  ).bind(mockId, expectedRevision).first<{ present: number }>();
  return current ? "invalid_state" : "stale_mock";
}

export type UndoMockResult = "ok" | "stale_mock" | "no_user_decisions";

export async function undoLatestMockDecision(
  db: D1Database,
  mockId: string,
  expectedRevision: number,
): Promise<UndoMockResult> {
  const checkpoint = await db.prepare(
    `SELECT decision_overall_pick, pick_count_before, rng_state_before
       FROM mock_checkpoints
      WHERE mock_id = ?
      ORDER BY decision_overall_pick DESC
      LIMIT 1`,
  ).bind(mockId).first<{
    decision_overall_pick: number;
    pick_count_before: number;
    rng_state_before: number;
  }>();
  if (!checkpoint) {
    const current = await db.prepare(
      "SELECT 1 AS present FROM mock_drafts WHERE id = ? AND revision = ?",
    ).bind(mockId, expectedRevision).first<{ present: number }>();
    return current ? "no_user_decisions" : "stale_mock";
  }

  const guard = `EXISTS (
    SELECT 1 FROM mock_drafts WHERE id = ? AND revision = ?
  )`;
  const results = await db.batch([
    db.prepare(
      `DELETE FROM mock_picks
        WHERE mock_id = ? AND overall_pick > ? AND ${guard}`,
    ).bind(
      mockId,
      checkpoint.pick_count_before,
      mockId,
      expectedRevision,
    ),
    db.prepare(
      `DELETE FROM mock_checkpoints
        WHERE mock_id = ? AND decision_overall_pick = ? AND ${guard}`,
    ).bind(
      mockId,
      checkpoint.decision_overall_pick,
      mockId,
      expectedRevision,
    ),
    db.prepare(
      `UPDATE mock_drafts
          SET rng_state = ?, status = 'active', revision = ?, updated_at = ?
        WHERE id = ? AND revision = ?`,
    ).bind(
      checkpoint.rng_state_before,
      expectedRevision + 1,
      isoNow(),
      mockId,
      expectedRevision,
    ),
  ]);
  return results.at(-1)?.meta.changes === 1 ? "ok" : "stale_mock";
}

export type ResetMockResult = "ok" | "stale_mock" | "invalid_state";

export async function resetCurrentMock(
  db: D1Database,
  mockId: string,
  expectedRevision: number,
  restarted: MockAggregate,
): Promise<ResetMockResult> {
  const row = await db.prepare(
    `SELECT seed, strategy_version, user_slot, team_count, rounds, variance_preset
       FROM mock_drafts
      WHERE id = ? AND revision = ?`,
  ).bind(mockId, expectedRevision).first<{
    seed: number;
    strategy_version: string;
    user_slot: number;
    team_count: number;
    rounds: number;
    variance_preset: VariancePreset;
  }>();
  if (!row) return "stale_mock";
  if (
    row.seed !== restarted.seed
    || row.strategy_version !== restarted.strategy_version
    || row.user_slot !== restarted.user_slot
    || row.team_count !== restarted.team_count
    || row.rounds !== restarted.rounds
    || row.variance_preset !== restarted.variance_preset
  ) {
    return "invalid_state";
  }

  const guard = `EXISTS (
    SELECT 1 FROM mock_drafts WHERE id = ? AND revision = ?
  )`;
  const now = isoNow();
  const results = await db.batch([
    db.prepare(
      `DELETE FROM mock_checkpoints WHERE mock_id = ? AND ${guard}`,
    ).bind(mockId, mockId, expectedRevision),
    db.prepare(
      `DELETE FROM mock_picks WHERE mock_id = ? AND ${guard}`,
    ).bind(mockId, mockId, expectedRevision),
    ...restarted.picks.map((pick) =>
      db.prepare(
        `INSERT INTO mock_picks
          (mock_id, overall_pick, round, round_pick, draft_slot, player_key,
           player_name, player_pos, player_team, source, picked_at)
         SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
          WHERE ${guard}`,
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
      )
    ),
    db.prepare(
      `UPDATE mock_drafts
          SET rng_state = ?, status = ?, paused = 0, revision = ?, updated_at = ?
        WHERE id = ? AND revision = ?`,
    ).bind(
      restarted.rng_state,
      restarted.complete ? "complete" : "active",
      expectedRevision + 1,
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
  expectedRevision: number,
): Promise<DiscardMockResult> {
  const current = await db
    .prepare(
      `SELECT id, revision FROM mock_drafts
        ORDER BY CASE WHEN status = 'active' THEN 0 ELSE 1 END, updated_at DESC
        LIMIT 1`,
    )
    .first<{ id: string; revision: number }>();
  if (current?.id !== mockId || current.revision !== expectedRevision) return "stale_mock";
  const guard = `EXISTS (
    SELECT 1 FROM mock_drafts WHERE id = ? AND revision = ?
  )`;
  const results = await db.batch([
    db.prepare(
      `DELETE FROM mock_checkpoints
        WHERE (mock_id = ?
           OR mock_id IN (SELECT id FROM mock_drafts WHERE status = 'complete'))
          AND ${guard}`,
    ).bind(mockId, mockId, expectedRevision),
    db.prepare(
      `DELETE FROM mock_picks
        WHERE (mock_id = ?
           OR mock_id IN (SELECT id FROM mock_drafts WHERE status = 'complete'))
          AND ${guard}`,
    ).bind(mockId, mockId, expectedRevision),
    db.prepare(
      `DELETE FROM mock_teams
        WHERE (mock_id = ?
           OR mock_id IN (SELECT id FROM mock_drafts WHERE status = 'complete'))
          AND ${guard}`,
    ).bind(mockId, mockId, expectedRevision),
    db.prepare(
      `DELETE FROM mock_drafts
        WHERE status = 'complete' AND id <> ? AND ${guard}`,
    ).bind(mockId, mockId, expectedRevision),
    db.prepare("DELETE FROM mock_drafts WHERE id = ? AND revision = ?")
      .bind(mockId, expectedRevision),
  ]);
  return results.at(-1)?.meta.changes === 1 ? "ok" : "stale_mock";
}

export function unconfiguredMockState(): MockState {
  return { configured: false, picks: [], revision: 0 };
}
