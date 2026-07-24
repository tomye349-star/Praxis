-- Run this once against your EXISTING database (you already ran the original
-- schema.sql, so re-running that won't add these new bits to the tables you
-- already have). This is what lets Canvas/SBHS automation sync data in
-- without duplicating rows on every run, and gives us somewhere private to
-- store rotating OAuth refresh tokens.

ALTER TABLE assignments ADD COLUMN source_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_assignments_source_id ON assignments(source_id) WHERE source_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS kv_store (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
