# Wasabi storage (BagIt unpacked)

Press and dump ingest upload **unpacked RFC 8493 BagIt** trees to the `hitsave-incoming` bucket.

| Ingest kind | Script | S3 layout |
|-------------|--------|-----------|
| `press_material` | `upload-press-bag-wasabi.py` | `press-material/{sort_letter}/{package_id}/` |
| `dump` | `upload-dump-wasabi.py` | prefix from `ingest-kinds.yaml` dump layout |

Omeka reads packages via S3 API — see [dip-wasabi-s3.md](./dip-wasabi-s3.md).

## Setup

1. Create a Wasabi bucket in the region matching `config/preservation/ingest.yaml` (default `us-east-2`).
2. Store credentials in the private config repo (`secrets/wasabi-credentials.yaml`).
3. Set `wasabi.enabled: true` and `wasabi.bucket` in `ingest.yaml`.
4. Apply ledger migrations if needed: `bash scripts/apply-ledger-migrations.sh`.
5. Rebuild workers: `docker compose build ingest-worker`.

## Full ingest

With `wasabi.enabled: true`, `ingest-game-folder.py` runs ClamAV → BagIt build → Wasabi sync → ledger `complete` with `package_storage_prefix` and `inventory_json`.

Register in Omeka is a separate step: `docker compose run --rm omeka-catalog <game_key> …`.
