# E2E preservation batch (multi-folder + test Omeka)

Use when **`hitsave-archiver`**, **`hitsave-archiver-config`**, and **`hitsave-omeka-test`** run on one machine (see [hitsave-omeka-test `docs/build-real-dip.md`](https://github.com/hitsave/hitsave-omeka-test/blob/main/docs/build-real-dip.md)).

## Host paths (private `host.env`, not in git)

Set in **`hitsave-archiver-config/host.env`** (from `config/host.env.example` in this repo):

| Variable | Role |
|----------|------|
| `HOST_OUTPUT` | Host directory mounted as `/output` (BagIt bags under `bag/…`) |
| `HOST_PRESS_MATERIAL` | Press material mounted read-only at `/data/press-material` |
| `HITSAVE_PRIVATE_CONFIG` | Private config checkout (secrets, operator `database.yaml`) |

```bash
source /path/to/hitsave-archiver-config/host.env
```

Reuse an existing DIP tree by pointing `HOST_OUTPUT` at that directory before compose or batch scripts.

## Batch configs (local, not in git)

Batch manifests are **operator-local** (see `AGENTS.md`). Start from the template:

```bash
cp config/preservation/batch.yml.example config/preservation/batch-my-run.yml
# edit source_subpath / select, then:
python3 scripts/sync-preservation-config.py
./scripts/batch-expand-preservation.sh config/preservation/batch-my-run.yml
```

`batch_key` is the slug in the batch file name (`batch-my-run.yml` → `my-run`). Generated per-game YAML lives under `config/preservation/generated/<batch_key>/` (three fields per game; ledger **`game_key`** is assigned at ingest).

Single-game pilot (committed): `config/preservation/pilot-game.yaml`.

## Full E2E: ingest + Omeka catalog registration

Test Omeka must be running (`run-omeka-test.sh` in **hitsave-omeka-test**).

```bash
cd hitsave-archiver
docker compose up -d postgres clamav
bash scripts/run-batch-resume-omeka.sh config/preservation/batch-my-run.yml
docker compose run --rm -T ingest-worker /config/preservation/pilot-game.yaml </dev/null
docker compose run --rm -T omeka-catalog /config/preservation/pilot-game.yaml </dev/null
```

`run-batch-resume-omeka.sh` skips **ingest** when ledger `status=complete`, then runs **omeka-catalog** only when ingest finished `complete` (quarantine/failed rows are skipped). Ingest or Omeka errors **log and continue** to the next game in the manifest.

**Content policy:** With `content_policy.enforce: true` in [`ingest.yaml`](../config/preservation/ingest.yaml) (default), booru-style filenames **abort ingest** and write under `$HOST_OUTPUT/quarantine/<game_key>/`. Moby `no_match` is recorded on the ledger only (`mobygames.block_on_failure: false`). Set `enforce: false` only for permissive test runs (warn in logs, no quarantine).

**Moby title search:** API hits must pass [`moby_title_matches_search`](../scripts/moby_resolve.py) (every significant search word appears in the Moby title). Optional DDG fallback ([`mobygames.yaml`](../config/mobygames.yaml) `web_search_fallback`) walks up to `max_results` Moby URLs from `"{title} mobygames"` and accepts the first that passes the same check—not the first URL blindly.

### Ingest only (ledger + Wasabi, no new Omeka items)

```bash
bash scripts/run-batch-resume-omeka.sh --ingest-only config/preservation/batch-my-run.yml
```

## Catalog register only (ingest already complete)

When ledger rows are **`complete`** and Wasabi has the bag prefix, upload Omeka items only:

```bash
bash scripts/register-manifest-dips-omeka.sh config/preservation/batch-my-run.yml
bash scripts/register-manifest-dips-omeka.sh --pilot
```

Log: `.generated/register-manifest-dips-omeka.log` (override with `HITSAVE_REGISTER_LOG`).

**Note:** `docker compose run` must use `-T` and `</dev/null>` so Compose does not consume the manifest stdin (see script).

## Verify

- Ledger: `docker compose exec -T postgres psql -U hitsave -d hitsave_ledger -c 'SELECT game_key, status, omeka_item_id FROM game_ingest ORDER BY game_key LIMIT 20;'`
- Omeka admin and public site URLs come from **`hitsave-omeka-test`** `config/omeka-test/settings.yaml` (`omeka.public_url`, `omeka.site_slug`).
