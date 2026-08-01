ALTER TABLE mock_drafts
  ADD COLUMN paused INTEGER NOT NULL DEFAULT 0 CHECK (paused IN (0, 1));

CREATE TABLE mock_checkpoints (
  mock_id TEXT NOT NULL REFERENCES mock_drafts(id),
  decision_overall_pick INTEGER NOT NULL CHECK (decision_overall_pick > 0),
  pick_count_before INTEGER NOT NULL CHECK (pick_count_before >= 0),
  rng_state_before INTEGER NOT NULL CHECK (rng_state_before BETWEEN 0 AND 4294967295),
  created_at TEXT NOT NULL,
  PRIMARY KEY (mock_id, decision_overall_pick)
);

CREATE INDEX mock_checkpoints_latest
  ON mock_checkpoints(mock_id, decision_overall_pick);
