# Agent guide — hitsave-archiver

## Repos

- **Public:** this repo — no credentials in git.
- **Private:** `hitsave-archiver-config` — Wasabi, Omeka API, MobyGames, ledger Postgres password.
- **Omeka QA:** `hitsave-omeka-test` — local MariaDB/Omeka stack (not in this repo).
- **Theme:** `hitsave-archive-theme` — rebuild/deploy separately.

Compose expects `HITSAVE_PRIVATE_CONFIG` (default sibling `../hitsave-archiver-config`). Run `./scripts/ensure-local-config.sh` after clone.

**Cursor:** Glob rules in **`.cursor/rules/`** (compose/ingest, `config/**`). Parent **`hitsave-e2e/`** has prefixed rules when that folder is the workspace.

**Preservation direction:** OAIS + BagIt (unpacked on Wasabi, payload under `data/`). See **`docs/oais-bagit-preservation-plan.md`** and **`config/preservation/submission-sources.yaml`**.

**Remote intake lanes:** **`docs/remote-intake-lanes.md`** (intake handoff → archiver; private lane inventory template in `config/preservation/lane-inventory.template.yaml`).

**Ops UI:** preservation **status-web** on `:8090` (ledger, health, catalog **`/correct`**) — config from **`config/status-web.yaml.example`**. **Windmill** (schedules, job logs): **`windmill_ops/`**, **`docs/design-notes/windmill-ops-spike.md`**, **`./scripts/windmill-sync-push.sh`**.

## Docker

Use `docker compose` (ingest worker, omeka-catalog, CI fixtures). Do not run preservation scripts with bare host Python when the stack defines the runtime.

After changing `ingest-worker/` or submission security code, run:

```bash
CI=1 ./scripts/test-submission-security-fixtures.sh
```

## Git

Ship public changes via PR when this repo uses branch protection; push private config only to **hitsave-archiver-config** (private remote).

**Do not commit** operator-specific preservation batch manifests under `config/preservation/` (real tank paths, one-off game lists, local batch names). Copy `batch.yml.example` to a local `batch.yml` or another ignored `batch-*.yml` path. Never commit `data/` from test runs.
