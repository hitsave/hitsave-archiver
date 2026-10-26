# Remote intake operator scripts

Community dump pipelines (Google Drive and Wasabi incoming → **hitsave-gdrive-intake** → **ingest-worker** → prod Omeka). SSOT: [docs/remote-intake-lanes.md](../../docs/remote-intake-lanes.md).

## Prerequisites

- `hitsave-archiver-config` on `HITSAVE_PRIVATE_CONFIG` / `host.env`.
- **Poll** uses the **Google Drive / Wasabi APIs** via the `gdrive-intake` container (`docker-compose.gdrive-intake.yml` from the archiver root). It does **not** scan `HOST_GDRIVE_INTAKE` for new packages.
- Set **`HITSAVE_GDRIVE_INTAKE_ROOT`** (or **`HITSAVE_GDRIVE_INTAKE_CONFIG_DIR`** + **`HITSAVE_GDRIVE_INTAKE_SECRETS_DIR`**) to the gdrive-intake checkout or config tree — no need for a sibling directory layout if paths are explicit.
- `source hitsave-archiver-config/host.env` — **`HOST_GDRIVE_INTAKE`**, **`HOST_OUTPUT`**, **`HITSAVE_PRIVATE_CONFIG`**.
- Prod Omeka catalog overlay at **`$HITSAVE_PRIVATE_CONFIG/omeka-catalog.yaml`** (for `register-dump-omeka.sh`).

Logs and poll JSON default to **`hitsave-archiver/.generated/`** (override with **`HITSAVE_REMOTE_INTAKE_LOG_DIR`**).

Poll JSON under `.generated/` is for sweeps and move-to-processed scripts only. **Ops visibility:** `game_ingest` ledger + status-web **Health** (not a separate inbox poll UI).

## Main commands

| Script | Role |
|--------|------|
| `run-gdrive-redump-per-folder.sh` | Drive: stage → ingest → Omeka per package |
| `run-wasabi-redump-per-folder.sh` | Wasabi incoming: stage → ingest → Omeka per package |
| `run-gdrive-full-batch.sh` | Rebuild images + Drive per-folder + reconcile |
| `run-wasabi-redump-full-batch.sh` | Rebuild images + Wasabi per-folder + reconcile |
| `register-dump-omeka.sh` | One dump on prod Omeka (`--slug` or `--game-key`) |
| `register-prod-dumps-omeka.sh` | Reconcile ledger `complete` rows missing `omeka_item_id` |
| `acceptance-redump-package.sh` | Phase 1 spot-check: ledger row + dump validator summary (`--slug` or `--game-key`) |
| `move-gdrive-redump-to-processed.sh` | Poll Drive inbox; move ledger-complete packages with a disc image to `processed/` (`--execute`) |
| `move_wasabi_intake_to_processed.py` | Move ledger-complete Wasabi incoming SIP trees under `processed/` (poll-driven batch) |
| `move-wasabi-redump-to-processed.sh` | Poll Wasabi incoming + MOVE complete packages to `processed/` |
| `move-wasabi-incoming-prefix-to-processed.sh` | MOVE one incoming prefix tree to `processed/` (`--prefix`, `--execute`) |
| `flatten-gamewavedvd-wasabi.py` | Flatten nested GameWaveDVD folder layout on incoming Wasabi (dry-run default) |
| `check-wasabi-inbox-processed.py` | One-off: compare poll JSON vs Wasabi incoming/`processed/` vs ledger-complete rows |

Poll helpers (sweep internals): `gdrive-redump-poll-packages.sh`, `wasabi-redump-poll-packages.sh`.

Drive poll skips folders named `processed` and packages with **no disc image** (sidecar-only stubs — ingest from Wasabi instead). See [remote-intake-lanes.md](../../docs/remote-intake-lanes.md) acceptance checklist.

Example:

```bash
source /path/to/hitsave-archiver-config/host.env
cd /path/to/hitsave-archiver
./scripts/remote-intake/run-gdrive-redump-per-folder.sh
```
