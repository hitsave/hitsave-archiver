-- Where an intake package came from (Drive folder or Wasabi prefix), copied from its handoff
-- manifest (ready/<slug>/hitsave-submission.yaml) at ingest. Canonical form, see
-- scripts/intake_source.py:  drive:<folder id>  |  wasabi:<prefix/package folder>  |  local:<path>
-- The remote-intake slug-collision guard compares against this first, so it doesn't depend on
-- the intake host's scratch disk. Rows from before this column were filled in once, on 2026-11-04.
ALTER TABLE game_ingest
    ADD COLUMN IF NOT EXISTS intake_source TEXT;

CREATE INDEX IF NOT EXISTS idx_game_ingest_intake_source ON game_ingest (intake_source);
