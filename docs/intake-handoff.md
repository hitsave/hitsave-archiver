# Google Drive intake handoff (community dumps)

**Lane overview (all remote ingest types):** [remote-intake-lanes.md](./remote-intake-lanes.md).

Remote contributors upload **community dumps** (Redump, No-Intro, and other org sidecars) to Google Drive or Wasabi incoming; [**hitsave-intake**](https://github.com/hitsave/hitsave-intake) downloads, **verifies sidecar checksums**, and writes a local **ready** tree. **This repo** runs the preservation pipeline (ClamAV → BagIt → Wasabi `hitsave-dumps` → ledger → Omeka catalog). **Redump** is the reference sidecar implemented today; other org formats follow the same flow as parsers land.

**Full Drive batch (operator):** [`scripts/remote-intake/run-gdrive-redump-per-folder.sh`](../scripts/remote-intake/run-gdrive-redump-per-folder.sh) (stage → ingest → Omeka per package). Wasabi incoming: `run-wasabi-redump-per-folder.sh` in the same directory.

## Layout after gdrive `process`

Mount the intake **ready** tree into the ingest-worker (default host: `/tank/hitsave-archiver/intake/ready` → container `/data/intake/ready`; set `HOST_INTAKE` in `host.env` if different):

```text
/data/intake/ready/{game_key}/
  game.ingest.yaml
  hitsave-submission.yaml
  !submissionInfo.txt
  *.iso / *.bin …
```

`game.ingest.yaml` is produced by intake with `source_game_folder` pointing at that SIP folder (via `handoff.archiver_source_root` in hitsave-intake `config/intake.yaml`).

## Ingest one package

Requires a matching entry in `config/preservation/submission-sources.yaml` (`container_path_prefix: /data/intake/ready`).

```bash
docker compose run --rm ingest-worker \
  /data/intake/ready/<game_key>/game.ingest.yaml
```

Dump classification, Wasabi prefix (`dumps/{System}/{Category}/{game_key}/`), and BagIt upload use the same code path as on-site dump ingest ([`dump-preservation.md`](./dump-preservation.md)).

## Omeka (optional)

After each ingest (or on reconcile): **`scripts/remote-intake/register-dump-omeka.sh --slug <ready-folder>`**. Per-folder batch scripts call this automatically; **`register-prod-dumps-omeka.sh`** reconciles rows still missing `omeka_item_id`. See [design note](./design-notes/gdrive-redump-omeka-after-ingest.md).

```bash
docker compose run --rm omeka-catalog \
  /app/scripts/apply_dump_catalog_omeka.py \
  …
```

(Use the per-game register YAML + handoff paths documented in `register-prod-dumps-omeka.sh` / `apply_dump_catalog_omeka.py`.)

## What stays in intake only

- Google Drive API poll and quiescence (`upload_stable_seconds`)
- Resumable download
- **MD5/SHA1/Size fixity** against `!submissionInfo.txt` (fail closed before handoff)

No Wasabi credentials are required in the intake container.
