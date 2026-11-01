# Design note: Omeka catalog after each GDrive Redump ingest

**Status:** implemented  
**Context:** Operator scripts under `hitsave-e2e/`.

## Pipeline

`run-gdrive-redump-per-folder.sh` (used by `run-gdrive-full-batch.sh`) for each Drive package:

1. **Stage** from Drive if `ready/<slug>/game.ingest.yaml` is missing  
2. **Ingest** if ledger is not `complete`  
3. **`register-dump-omeka.sh --slug <slug>`** when ledger is `complete` (after ingest, or on skip-if-already-ingested)

Omeka errors are logged; ingest status is unchanged. `register-prod-dumps-omeka.sh` at batch end only rows with **`omeka_item_id IS NULL`** (reconcile).

## Single-package register

**`register-dump-omeka.sh`**

- Args: `--slug <ready-folder>` or `--game-key <ledger key>`; `--force` to patch even when `omeka_item_id` is set  
- Prod catalog: `HITSAVE_PRIVATE_CONFIG/omeka-catalog.yaml`  
- Title: `omeka_item_title` from `hitsave-submission.yaml`, then legacy uuid overrides, then `game_key`  
- Runs `apply_dump_catalog_omeka.py` via `omeka-catalog` compose service  

## Manual / legacy batch scripts

- **`ingest-gdrive-all-redump.sh`** — calls `register-dump-omeka.sh` after each successful ingest  
- **`process-gdrive-all-redump.sh`** + ingest-all — stage-all / ingest-all without per-folder staging (no Omeka between stages)  

## Preconditions

Omeka dump catalog needs handoff, ledger `complete`, and Wasabi upload (done inside ingest before `complete`).

## Related docs

- [Intake handoff](../intake-handoff.md)  
- [E2E preservation batch](../e2e-preservation-batch.md) — `run-batch-resume-omeka.sh` for press material  
