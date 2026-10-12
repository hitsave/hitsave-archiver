# Wasabi S3 for Omeka (BagIt unpacked)

Press and dump ingest upload **unpacked BagIt** trees to Wasabi. Omeka never stores package bytes locally.

## Flow

1. **Ingest** — `build-bagit-package.py` → `upload-press-bag-wasabi.py` or `upload-dump-wasabi.py`
2. **Ledger** — `package_storage_prefix`, `inventory_json`, local bag under `$HOST_OUTPUT/bag/…`
3. **Omeka** — `upload-dip-omeka-api.py` creates `omeka_dip_package` media with `package_storage_prefix` + `dip_wasabi_uri`
4. **Browse** — OmekaDipViewer **0.5+** reads objects via S3 (`dip-s3.config.php`)

## Test stack

See **hitsave-omeka-test** [dip-wasabi-s3-direct.md](https://github.com/hitsave/hitsave-omeka-test/blob/main/docs/dip-wasabi-s3-direct.md): `docker-compose.s3-direct.yml` + rendered `dip-s3.config.php`.

## Staff AIP URI

`omeka_staff_metadata.py` sets a staff-only `dcterms:relation` URI from ledger `package_storage_prefix` (or legacy rows).

## Removed paths

FUSE/rclone mounts, `external_tar_path`, and single-object `.tar` uploads are **not** used for new ingest.
