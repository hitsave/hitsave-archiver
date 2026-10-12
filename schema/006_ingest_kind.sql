-- Ingest kind (press_material, dump, other) for catalog enrichment routing.

ALTER TABLE game_ingest
    ADD COLUMN IF NOT EXISTS ingest_kind TEXT;

CREATE INDEX IF NOT EXISTS idx_game_ingest_ingest_kind ON game_ingest (ingest_kind);
