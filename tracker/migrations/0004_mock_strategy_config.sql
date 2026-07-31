ALTER TABLE mock_drafts
ADD COLUMN variance_preset TEXT NOT NULL DEFAULT 'realistic'
CHECK (variance_preset IN ('calm', 'realistic', 'wild'));
