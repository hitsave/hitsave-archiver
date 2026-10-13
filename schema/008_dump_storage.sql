-- Dump ingest: unpacked Wasabi layout (system/category) vs press tar.
ALTER TABLE game_ingest
    ADD COLUMN IF NOT EXISTS storage_layout TEXT,
    ADD COLUMN IF NOT EXISTS package_storage_prefix TEXT,
    ADD COLUMN IF NOT EXISTS dump_class TEXT,
    ADD COLUMN IF NOT EXISTS dump_system TEXT,
    ADD COLUMN IF NOT EXISTS dump_category TEXT;

CREATE INDEX IF NOT EXISTS idx_game_ingest_dump_class ON game_ingest (dump_class);
CREATE INDEX IF NOT EXISTS idx_game_ingest_package_storage_prefix ON game_ingest (package_storage_prefix);
