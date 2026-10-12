-- BagIt packaging fields (OAIS press ingest).

ALTER TABLE game_ingest
    ADD COLUMN IF NOT EXISTS package_format TEXT,
    ADD COLUMN IF NOT EXISTS submission_source_id TEXT,
    ADD COLUMN IF NOT EXISTS package_tar_sha256 TEXT,
    ADD COLUMN IF NOT EXISTS package_tar_key TEXT,
    ADD COLUMN IF NOT EXISTS inventory_json JSONB;
