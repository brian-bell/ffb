CREATE TABLE drafts (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  name TEXT NOT NULL,
  rounds INTEGER NOT NULL CHECK (rounds BETWEEN 1 AND 30),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE teams (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  draft_id INTEGER NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  draft_slot INTEGER NOT NULL CHECK (draft_slot >= 0),
  is_user INTEGER NOT NULL DEFAULT 0 CHECK (is_user IN (0, 1)),
  UNIQUE(draft_id, draft_slot),
  UNIQUE(draft_id, name)
);

CREATE UNIQUE INDEX one_user_team_per_draft ON teams(draft_id) WHERE is_user = 1;

CREATE TABLE picks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  draft_id INTEGER NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
  overall_pick INTEGER NOT NULL CHECK (overall_pick > 0),
  round INTEGER NOT NULL CHECK (round > 0),
  round_pick INTEGER NOT NULL CHECK (round_pick > 0),
  team_id INTEGER NOT NULL REFERENCES teams(id),
  player_key TEXT NOT NULL,
  player_name TEXT NOT NULL,
  player_pos TEXT,
  player_team TEXT,
  picked_at TEXT NOT NULL,
  UNIQUE(draft_id, overall_pick),
  UNIQUE(draft_id, player_key)
);

CREATE INDEX picks_by_team ON picks(draft_id, team_id, overall_pick);
