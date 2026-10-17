# Remote intake operator scripts

Community dump pipelines (Google Drive and Wasabi incoming → **hitsave-gdrive-intake** → **ingest-worker** → prod Omeka). SSOT: [docs/remote-intake-lanes.md](../../docs/remote-intake-lanes.md).

## Prerequisites

- Sibling checkouts: `hitsave-archiver`, `hitsave-archiver-config`, `hitsave-gdrive-intake` (or set `HITSAVE_GDRIVE_INTAKE_ROOT`, `HITSAVE_PRIVATE_CONFIG`, `HITSAVE_BUNDLE_ROOT`).
- `source hitsave-archiver-config/host.env` — **`HOST_GDRIVE_INTAKE`**, **`HOST_OUTPUT`**, **`HITSAVE_PRIVATE_CONFIG`**.
- Prod Omeka catalog overlay at **`$HITSAVE_PRIVATE_CONFIG/omeka-catalog.yaml`** (for `register-dump-omeka.sh`).

Logs and poll JSON default to **`hitsave-archiver/.generated/`** (override with **`HITSAVE_REMOTE_INTAKE_LOG_DIR`**).

## Main commands

| Script | Role |
|--------|------|
| `run-gdrive-redump-per-folder.sh` | Drive: stage → ingest → Omeka per package |
| `run-wasabi-redump-per-folder.sh` | Wasabi incoming: stage → ingest → Omeka per package |
| `run-gdrive-full-batch.sh` | Rebuild images + Drive per-folder + reconcile |
| `run-wasabi-redump-full-batch.sh` | Rebuild images + Wasabi per-folder + reconcile |
| `register-dump-omeka.sh` | One dump on prod Omeka (`--slug` or `--game-key`) |
| `register-prod-dumps-omeka.sh` | Reconcile ledger `complete` rows missing `omeka_item_id` |

Poll helpers: `gdrive-redump-poll-packages.sh`, `wasabi-redump-poll-packages.sh`.

Example:

```bash
source /path/to/hitsave-archiver-config/host.env
cd /path/to/hitsave-archiver
./scripts/remote-intake/run-gdrive-redump-per-folder.sh
```
