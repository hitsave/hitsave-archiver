-- Disc contents of data-disc dumps (PC/Mac CDs): files extracted from the disc image into a
-- second BagIt package (hitsave-dumps, disc-contents/<same path as the dump>) and shown on the
-- Omeka item. See scripts/disc_contents_policy.py for how a disc is classified.
--   disc_contents_class    press_kit | promotional | retail | not_data_disc
--   disc_contents_display  public | review | none | skip   (staff may change review -> public/none)
--   disc_contents_reasons  why (classification, scan results), human readable
--   disc_contents_prefix   storage prefix of the contents bag
--   disc_contents_media_id Omeka media showing it (private until public)
--   disc_contents_summary  counts and sizes per kind (images, videos, documents, programs, other)
ALTER TABLE game_ingest
    ADD COLUMN IF NOT EXISTS disc_contents_class TEXT,
    ADD COLUMN IF NOT EXISTS disc_contents_display TEXT,
    ADD COLUMN IF NOT EXISTS disc_contents_reasons TEXT,
    ADD COLUMN IF NOT EXISTS disc_contents_prefix TEXT,
    ADD COLUMN IF NOT EXISTS disc_contents_media_id INTEGER,
    ADD COLUMN IF NOT EXISTS disc_contents_summary JSONB,
    ADD COLUMN IF NOT EXISTS disc_contents_updated_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_game_ingest_disc_contents_display ON game_ingest (disc_contents_display);
