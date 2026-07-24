CREATE TABLE IF NOT EXISTS assignments (
  id TEXT PRIMARY KEY,
  subject TEXT NOT NULL,
  title TEXT NOT NULL,
  due_date TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  source_id TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_assignments_source_id ON assignments(source_id) WHERE source_id IS NOT NULL;

-- Generic key/value store: used for the timetable feed, the merged fitness
-- activity feed, and rotating OAuth refresh tokens (SBHS, later Strava) so
-- automation can update its own credentials without ever touching GitHub secrets.
CREATE TABLE IF NOT EXISTS kv_store (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS study_logs (
  id TEXT PRIMARY KEY,
  assignment_id TEXT,
  minutes INTEGER NOT NULL,
  note TEXT,
  logged_at TEXT NOT NULL DEFAULT (datetime('now')),
  -- Snapshotted at log time so subject totals and history stay correct
  -- forever, even after the linked assignment is edited/archived/deleted.
  subject TEXT,
  assignment_title TEXT,
  FOREIGN KEY (assignment_id) REFERENCES assignments(id)
);

-- A few starter rows so the dashboard isn't empty on first load.
-- Feel free to delete these once your real assignments are in.
INSERT INTO assignments (id, subject, title, due_date, status) VALUES
  ('seed-1', 'Physics', 'Report', date('now', '+2 days'), 'active'),
  ('seed-2', 'English', 'Essay draft', date('now', '+5 days'), 'active'),
  ('seed-3', 'Chemistry', 'Prac writeup', date('now', '+9 days'), 'active'),
  ('seed-4', 'Maths', 'Problem set 4', date('now', '+14 days'), 'active');
