-- Package identity: sip_fingerprint dedup, UUID v7 package_id, Wasabi letter buckets.

ALTER TABLE game_ingest
    ADD COLUMN IF NOT EXISTS sip_fingerprint TEXT,
    ADD COLUMN IF NOT EXISTS package_id TEXT,
    ADD COLUMN IF NOT EXISTS title_slug TEXT,
    ADD COLUMN IF NOT EXISTS sort_letter TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS game_ingest_sip_fingerprint_uq
    ON game_ingest (sip_fingerprint)
    WHERE sip_fingerprint IS NOT NULL;
