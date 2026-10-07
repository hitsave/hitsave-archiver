-- HitSave preservation ledger (ingest worker + fixity cron).

CREATE TABLE IF NOT EXISTS game_ingest (
    id BIGSERIAL PRIMARY KEY,
    game_key TEXT NOT NULL UNIQUE,
    source_path TEXT NOT NULL,
    status TEXT NOT NULL,
    dip_path TEXT,
    aip_bag_path TEXT,
    package_uuid TEXT,
    file_count INTEGER,
    total_bytes BIGINT,
    checksum_manifest_path TEXT,
    omeka_item_id INTEGER,
    omeka_media_id INTEGER,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS fixity_run (
    id BIGSERIAL PRIMARY KEY,
    game_ingest_id BIGINT NOT NULL REFERENCES game_ingest (id) ON DELETE CASCADE,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    outcome TEXT NOT NULL,
    files_checked INTEGER NOT NULL DEFAULT 0,
    detail TEXT
);

CREATE INDEX IF NOT EXISTS idx_game_ingest_status ON game_ingest (status);
CREATE INDEX IF NOT EXISTS idx_fixity_run_game ON fixity_run (game_ingest_id);
