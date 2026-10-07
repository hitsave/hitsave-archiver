-- MobyGames enrichment + cache (preservation ledger).

ALTER TABLE game_ingest
    ADD COLUMN IF NOT EXISTS moby_game_id INTEGER,
    ADD COLUMN IF NOT EXISTS moby_title TEXT,
    ADD COLUMN IF NOT EXISTS metadata_json JSONB,
    ADD COLUMN IF NOT EXISTS metadata_source TEXT,
    ADD COLUMN IF NOT EXISTS moby_status TEXT,
    ADD COLUMN IF NOT EXISTS block_reason TEXT;

CREATE TABLE IF NOT EXISTS mobygames_cache (
    cache_key TEXT PRIMARY KEY,
    response_json JSONB NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_game_ingest_moby_status ON game_ingest (moby_status);
