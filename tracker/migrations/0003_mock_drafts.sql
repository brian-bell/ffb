CREATE TABLE mock_boards (
  fingerprint TEXT PRIMARY KEY,
  board_json TEXT NOT NULL,
  board_version INTEGER NOT NULL,
  season INTEGER NOT NULL,
  generated_at TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE mock_drafts (
  id TEXT PRIMARY KEY,
  board_fingerprint TEXT NOT NULL REFERENCES mock_boards(fingerprint),
  status TEXT NOT NULL CHECK (status IN ('active', 'complete')),
  seed INTEGER NOT NULL CHECK (seed BETWEEN 0 AND 4294967295),
  rng_state INTEGER NOT NULL CHECK (rng_state BETWEEN 0 AND 4294967295),
  strategy_version TEXT NOT NULL,
  user_slot INTEGER NOT NULL CHECK (user_slot > 0),
  team_count INTEGER NOT NULL CHECK (team_count BETWEEN 2 AND 20),
  rounds INTEGER NOT NULL CHECK (rounds > 0),
  revision INTEGER NOT NULL CHECK (revision >= 0),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX one_active_mock ON mock_drafts(status) WHERE status = 'active';

CREATE TABLE mock_teams (
  mock_id TEXT NOT NULL REFERENCES mock_drafts(id),
  draft_slot INTEGER NOT NULL CHECK (draft_slot >= 0),
  name TEXT NOT NULL,
  is_user INTEGER NOT NULL CHECK (is_user IN (0, 1)),
  PRIMARY KEY (mock_id, draft_slot)
);

CREATE UNIQUE INDEX one_user_team_per_mock ON mock_teams(mock_id) WHERE is_user = 1;

CREATE TABLE mock_picks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mock_id TEXT NOT NULL REFERENCES mock_drafts(id),
  overall_pick INTEGER NOT NULL CHECK (overall_pick > 0),
  round INTEGER NOT NULL CHECK (round > 0),
  round_pick INTEGER NOT NULL CHECK (round_pick > 0),
  draft_slot INTEGER NOT NULL CHECK (draft_slot >= 0),
  player_key TEXT NOT NULL,
  player_name TEXT NOT NULL,
  player_pos TEXT,
  player_team TEXT,
  source TEXT NOT NULL CHECK (source IN ('user', 'simulated')),
  picked_at TEXT NOT NULL,
  UNIQUE (mock_id, overall_pick),
  UNIQUE (mock_id, player_key)
);

CREATE INDEX mock_picks_by_team ON mock_picks(mock_id, draft_slot, overall_pick);
