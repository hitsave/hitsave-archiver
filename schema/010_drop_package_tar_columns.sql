-- Tar package columns unused after unpacked BagIt-only ingest.

ALTER TABLE game_ingest DROP COLUMN IF EXISTS package_tar_sha256;
ALTER TABLE game_ingest DROP COLUMN IF EXISTS package_tar_key;
