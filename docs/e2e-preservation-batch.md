# E2E preservation batch (multi-game + test Omeka)

Use when **`hitsave-archiver`**, **`hitsave-archiver-config`**, and **`hitsave-omeka-test`** run on one machine (see [hitsave-omeka-test `docs/build-real-dip.md`](https://github.com/hitsave/hitsave-omeka-test/blob/main/docs/build-real-dip.md)).

## Host paths (private `host.env`, not in git)

Set in **`hitsave-archiver-config/host.env`** (from `config/host.env.example` in this repo):

| Variable | Role |
|----------|------|
| `HOST_OUTPUT` | Host directory mounted as `/output` (AIP/DIP trees, including `dip/…`) |
| `HOST_PRESS_MATERIAL` | Press material mounted read-only at `/data/press-material` |
| `HITSAVE_PRIVATE_CONFIG` | Private config checkout (secrets, operator `database.yaml`) |

```bash
source /path/to/hitsave-archiver-config/host.env
```

Reuse an existing DIP tree by pointing `HOST_OUTPUT` at that directory before compose or batch scripts.

## Batch configs (repo)

- `config/preservation/batch-repcopies-s.yml` — Representation_Copies/S (25 games)
- `config/preservation/batch-repcopies-t.yml` — Representation_Copies/T (25 games)
- `config/preservation/pilot-game.yaml` — single-game WoG1 pilot (`pilot-wog1.tar`)

Expand manifests (once per batch, or after changing `select`):

```bash
cd hitsave-archiver
python3 scripts/sync-preservation-config.py
./scripts/batch-expand-preservation.sh config/preservation/batch-repcopies-s.yml
./scripts/batch-expand-preservation.sh config/preservation/batch-repcopies-t.yml
```

Generated per-game YAML lives under `config/preservation/generated/<batch_key>/`.

## Full E2E: ingest + Omeka upload

Test Omeka must be running (`run-omeka-test.sh` in **hitsave-omeka-test**).

```bash
cd hitsave-archiver
docker compose up -d postgres clamav
bash scripts/run-batch-resume-omeka.sh config/preservation/batch-repcopies-s.yml
bash scripts/run-batch-resume-omeka.sh config/preservation/batch-repcopies-t.yml
docker compose run --rm -T ingest-worker /config/preservation/pilot-game.yaml </dev/null
docker compose run --rm -T omeka-uploader pilot-wog1 /config/preservation/pilot-game.yaml </dev/null
```

`run-batch-resume-omeka.sh` skips **ingest** when ledger `status=complete`, then runs **omeka-uploader** (skips when the item already verifies in Omeka).

### Ingest only (ledger / AIP, no new Omeka items)

```bash
bash scripts/run-batch-resume-omeka.sh --ingest-only config/preservation/batch-repcopies-s.yml
```

## Upload only (DIP .tar already on disk)

When DIPs exist under `$HOST_OUTPUT/dip/…` and you only need Omeka items:

```bash
bash scripts/upload-manifest-dips-omeka.sh config/preservation/batch-repcopies-s.yml
bash scripts/upload-manifest-dips-omeka.sh config/preservation/batch-repcopies-t.yml
bash scripts/upload-manifest-dips-omeka.sh --pilot
```

Log: `.generated/upload-manifest-dips-omeka.log` (override with `HITSAVE_UPLOAD_LOG`).

**Note:** `docker compose run` must use `-T` and `</dev/null` so Compose does not consume the manifest stdin (see script).

## Verify

- Ledger: `docker compose exec -T postgres psql -U hitsave -d hitsave_ledger -c 'SELECT game_key, status, omeka_item_id FROM game_ingest ORDER BY game_key LIMIT 20;'`
- Omeka admin and public site URLs come from **`hitsave-omeka-test`** `config/omeka-test/settings.yaml` (`omeka.public_url`, `omeka.site_slug`).
