# Agent guide — hitsave-archiver

## Repos

- **Public:** this repo — no credentials in git.
- **Private:** `hitsave-archiver-config` — Wasabi, Omeka API, MobyGames, ledger Postgres password.
- **Omeka QA:** `hitsave-omeka-test` — local MariaDB/Omeka stack (not in this repo).
- **Theme:** `hitsave-archive-theme` — rebuild/deploy separately.

Compose expects `HITSAVE_PRIVATE_CONFIG` (default sibling `../hitsave-archiver-config`). Run `./scripts/ensure-local-config.sh` after clone.

## Docker

Use `docker compose` (ingest worker, uploader, CI fixtures). Do not run preservation scripts with bare host Python when the stack defines the runtime.

After changing `ingest-worker/` or submission security code, run:

```bash
CI=1 ./scripts/test-submission-security-fixtures.sh
```

## Git

Ship public changes via PR when this repo uses branch protection; push private config only to **hitsave-archiver-config** (private remote).

**Do not commit** operator-specific preservation batch manifests under `config/preservation/` (real tank paths, one-off game lists, local batch names). Use `batch.yml.example` and private config or an untracked local file. Never commit `data/` from test runs. Legacy `batch-repcopies-*.yml` files predate this rule; do not add new batch YAML to git.
