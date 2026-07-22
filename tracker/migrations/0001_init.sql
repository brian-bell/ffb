-- Slice 6: provision D1 + prove the binding end-to-end with metadata only.
-- Pick state (teams, picks, draft_order) arrives in slice 7's migrations.
CREATE TABLE IF NOT EXISTS draft_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
-- e.g. ('board_generated_at', '2024-08-15T12:00:00Z'), ('schema_version', '1')
