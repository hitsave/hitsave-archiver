-- Wasabi AIP storage pointers (preservation ledger).

ALTER TABLE game_ingest
    ADD COLUMN IF NOT EXISTS aip_wasabi_prefix TEXT,
    ADD COLUMN IF NOT EXISTS aip_uploaded_at TIMESTAMPTZ;
