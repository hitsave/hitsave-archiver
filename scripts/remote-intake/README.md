# Remote intake operator scripts

Community dump pipelines (Google Drive and Wasabi incoming → **hitsave-intake** → **ingest-worker** → prod Omeka). SSOT: [docs/remote-intake-lanes.md](../../docs/remote-intake-lanes.md).

**Normally you don't run these by hand:** the Windmill job `remote_intake_sweep` runs `run-wasabi-redump-per-folder.sh` then `run-gdrive-redump-per-folder.sh` every 30 minutes ([windmill_ops/README.md](../../windmill_ops/README.md)). For a manual run, hold the shared lock first so a scheduled sweep can't overlap: `exec 9<.generated/windmill/remote-intake.lock && flock -n 9`, then set `HITSAVE_REMOTE_INTAKE_LOCK_HELD=1`.

## Prerequisites

- `hitsave-archiver-config` on `HITSAVE_PRIVATE_CONFIG` / `host.env`.
- **Poll** uses the **Google Drive / Wasabi APIs** via the `intake` container (`docker-compose.intake.yml` from the archiver root). It does **not** scan `HOST_INTAKE` for new packages.
- Intake config and the Drive key come from **`hitsave-archiver-config/intake/`** and **`intake/secrets/`** (falls back to the hitsave-intake checkout's `config/` and `secrets/`). Override with **`HITSAVE_INTAKE_CONFIG_DIR`** / **`HITSAVE_INTAKE_SECRETS_DIR`**. **`HITSAVE_INTAKE_ROOT`** points at the intake checkout (compose file and build context).
- `source hitsave-archiver-config/host.env` — **`HOST_INTAKE`**, **`HOST_OUTPUT`**, **`HITSAVE_PRIVATE_CONFIG`**.
- Prod Omeka catalog overlay at **`$HITSAVE_PRIVATE_CONFIG/omeka-catalog.yaml`** (for `register-dump-omeka.sh`).

Logs and poll JSON default to **`hitsave-archiver/.generated/`** (override with **`HITSAVE_REMOTE_INTAKE_LOG_DIR`**).

Poll JSON under `.generated/` is for sweeps and move-to-processed scripts only. **Ops visibility:** `game_ingest` ledger + the ledger's **Health** page (not a separate inbox poll UI).

## Main commands

| Script | Role |
|--------|------|
| `run-gdrive-redump-per-folder.sh` | Drive: stage → ingest → Omeka per package |
| `run-wasabi-redump-per-folder.sh` | Wasabi incoming: stage → ingest → Omeka per package |
| `register-dump-omeka.sh` | One dump on prod Omeka (`--slug` or `--game-key`) |
| `register-prod-dumps-omeka.sh` | Reconcile ledger `complete` rows missing `omeka_item_id` |
| `move_wasabi_package_to_processed.py` | One Wasabi package → `processed/{lane}/` (verified MOVE); the per-folder runner calls it once the ledger is complete |

Poll helpers (sweep internals): `gdrive-redump-poll-packages.sh`, `wasabi-redump-poll-packages.sh`.

Drive poll skips folders named `processed` and packages with **no disc image** (sidecar-only stubs — ingest from Wasabi instead). See [remote-intake-lanes.md](../../docs/remote-intake-lanes.md) acceptance checklist.

Example:

```bash
source /path/to/hitsave-archiver-config/host.env
cd /path/to/hitsave-archiver
./scripts/remote-intake/run-gdrive-redump-per-folder.sh
```

## DVD menu previews

Before each ingest the sweep runs `remote_intake_dvd_menu_preview <slug>` (common.sh): the
`dvd-menu-preview` compose service (image built from the private `hitsave/dvd-menu-preview`
checkout, `HITSAVE_DVD_MENU_PREVIEW_SRC`, default `../hitsave-dvd-menu-preview`; no network;
libdvdcss and the CSS descrambler stay out of this public repo and ingest-worker) checks `!submissionInfo.txt` for `System: DVD-Video`, renders the menu
from the ISO and writes `<HOST_OUTPUT>/dvd-menu/<slug>/{menu_preview.jpg,result.json}`.
`build-bagit-package.py` packs that still as the `menu` preview (first slide, browse image) in
front of the payload previews (scans). Failures only mean no menu slide; ingest continues.
Bags packaged before this: `scripts/apply_dvd_menu_bag_previews.py` (batch backfill).
