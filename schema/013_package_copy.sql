-- Copies of preservation packages outside Wasabi (docs/glacier-copy.md).
-- One row per package and copy location; scripts/glacier-copy.py upserts it after a verified copy.
--   location            aws-deep-archive (S3 Glacier Deep Archive)
--   bucket, prefix      where the copy lives (prefix = <wasabi bucket>/<package prefix>/)
--   object_count, bytes what the last copy covered (the package and its disc-contents bag)
--   tagmanifest_sha256  SHA-256 of the package's tagmanifest-sha256.txt when it was copied, then a
--                       comma and the disc-contents bag's when the dump has one; the copy is out
--                       of date when Wasabi's tag manifests differ
--   copied_at           last copy that uploaded or confirmed every file
--   verified_at         last scripts/glacier-copy-check.py pass
CREATE TABLE IF NOT EXISTS package_copy (
    id BIGSERIAL PRIMARY KEY,
    game_ingest_id BIGINT NOT NULL REFERENCES game_ingest (id) ON DELETE CASCADE,
    location TEXT NOT NULL,
    bucket TEXT NOT NULL,
    prefix TEXT NOT NULL,
    object_count INTEGER NOT NULL DEFAULT 0,
    bytes BIGINT NOT NULL DEFAULT 0,
    tagmanifest_sha256 TEXT,
    copied_at TIMESTAMPTZ,
    verified_at TIMESTAMPTZ,
    UNIQUE (game_ingest_id, location)
);
