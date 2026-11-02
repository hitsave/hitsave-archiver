# Wasabi S3 for Omeka (BagIt unpacked)

Press and dump ingest upload **unpacked BagIt** trees to the preservation buckets (`hitsave-press-material`, `hitsave-dumps`, … per ingest kind — [preservation-wasabi.md](./preservation-wasabi.md)). `hitsave-incoming` is only the contributor inbox, never a preservation target.

## Flow

1. **Ingest** — `build-bagit-package.py` → `upload-bag-wasabi.py`
2. **Ledger** — `package_storage_prefix`, `inventory_json`, temporary local bag under `$HOST_OUTPUT/bag/…` (pruned after Wasabi verification)
3. **Omeka** — `register-dip-omeka-api.py` creates `omeka_dip_package` media with `package_storage_prefix` and `dip_wasabi_uri`
4. **Browse** — OmekaDipViewer reads objects via S3 (`dip-s3.config.php`)

## Test stack

See **hitsave-omeka-test** [dip-wasabi-s3-direct.md](https://github.com/hitsave/hitsave-omeka-test/blob/main/docs/dip-wasabi-s3-direct.md): `docker-compose.s3-direct.yml` and `scripts/shell/render-dip-s3-config.py`.

## Staff AIP URI

`omeka_staff_metadata.py` sets a staff-only `dcterms:relation` URI from ledger `package_storage_prefix`.
