-- Run this once against your EXISTING database. Adds the columns needed for
-- delete/edit/unarchive on assignments and for accurate per-subject time
-- totals that survive an assignment later being edited, archived, or
-- deleted (the subject/title get snapshotted onto each log row at the
-- moment it's logged, instead of only being derived by joining back to the
-- assignments table).

ALTER TABLE study_logs ADD COLUMN subject TEXT;
ALTER TABLE study_logs ADD COLUMN assignment_title TEXT;
