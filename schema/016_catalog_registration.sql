-- Omeka catalog registration failures (docs/catalog-registration-retry.md).
--
-- register-dip-omeka-api.py writes these when it registers a complete package in Omeka:
--   catalog_error         the last registration error; NULL once registration succeeds
--   catalog_attempts      failed attempts since the last success
--   catalog_attempted_at  when registration last ran for this package
--   catalog_config_path   the generated game config registration was run with, so the
--                         retry job (Windmill retry_catalog_registration) can run it again
ALTER TABLE game_ingest ADD COLUMN IF NOT EXISTS catalog_error TEXT;
ALTER TABLE game_ingest ADD COLUMN IF NOT EXISTS catalog_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE game_ingest ADD COLUMN IF NOT EXISTS catalog_attempted_at TIMESTAMPTZ;
ALTER TABLE game_ingest ADD COLUMN IF NOT EXISTS catalog_config_path TEXT;

CREATE INDEX IF NOT EXISTS game_ingest_catalog_error ON game_ingest (catalog_attempted_at)
    WHERE catalog_error IS NOT NULL;

-- Complete press packages with no Omeka item failed registration before it was tracked.
-- Dumps and disc-contents bags are catalogued differently, so they are left alone.
UPDATE game_ingest
SET catalog_error = 'registration failed before failures were recorded (marked by schema 016)',
    catalog_attempts = 1
WHERE status = 'complete' AND omeka_item_id IS NULL AND ingest_kind = 'press_material'
  AND catalog_error IS NULL;
