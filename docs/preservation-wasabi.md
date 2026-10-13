# Wasabi storage (BagIt unpacked)

Press and dump ingest upload **unpacked RFC 8493 BagIt** trees to Wasabi. Keys are the same as before; **bucket** is chosen by ingest kind.

| Ingest kind | Script | Bucket (config) | S3 key layout |
|-------------|--------|-----------------|---------------|
| `press_material` | `upload-press-bag-wasabi.py` | `wasabi.buckets.press_material` | `press-material/{sort_letter}/{package_id}/` |
| `dump` | `upload-dump-wasabi.py` | `wasabi.buckets.dumps` | prefix from `ingest-kinds.yaml` dump layout |
| `other` | `upload-press-bag-wasabi.py` | `wasabi.buckets.other` | `other/{sort_letter}/{package_id}/` |
| `analog` | `upload-press-bag-wasabi.py` | `wasabi.buckets.analog` | `analog/{relative_sip_path}/` (folder tree under `paths.analog_material_root`, e.g. `VHS/USA/promotional/Nintendo/The Invasion of Nintendo`) |

Omeka reads **press** packages via S3 API — see [dip-wasabi-s3.md](./dip-wasabi-s3.md). Point Omeka `dip-s3.config.php` at the **press_material** bucket.

## Setup

1. Create one or two Wasabi buckets in the region matching `config/preservation/ingest.yaml` (default `us-east-2`).
2. Store credentials in the private config repo (`secrets/wasabi-credentials.yaml`).
3. Set `wasabi.enabled: true` and required `wasabi.buckets` entries for every ingest kind you use (`press_material`, `dumps`, `other`, …) in `ingest.yaml` (no fallback bucket).
4. Apply ledger migrations if needed: `bash scripts/apply-ledger-migrations.sh`.
5. Rebuild workers: `docker compose build ingest-worker`.

## Full ingest

With `wasabi.enabled: true`, `ingest-folder.py` runs ClamAV → BagIt build → Wasabi sync → ledger `complete` with `package_storage_prefix` and `inventory_json`.

Register in Omeka is a separate step: `docker compose run --rm omeka-catalog <game_key> …`.
