# Agent guide — hitsave-archiver

## Repos

- **Public:** this repo — no credentials in git.
- **Private:** `hitsave-archiver-config` — Wasabi, Omeka API, MobyGames, ledger Postgres password, host paths, intake / ledger / Windmill config.
- **Private:** `hitsave-intake` — Drive and Wasabi inbox polling and sidecar checksum verification.
- **Omeka QA:** `hitsave-omeka-test` — local MariaDB/Omeka stack (not in this repo).
- **Theme:** `hitsave-archive-theme` — rebuild/deploy separately.

Compose expects `HITSAVE_PRIVATE_CONFIG` (default sibling `../hitsave-archiver-config`). Run `./scripts/ensure-local-config.sh` after clone.

**Cursor:** Glob rules in **`.cursor/rules/`** (compose/ingest, `config/**`). Parent **`hitsave-e2e/`** has prefixed rules when that folder is the workspace.

**Preservation direction:** OAIS + BagIt (unpacked on Wasabi, payload under `data/`). See **`docs/oais-bagit-preservation-plan.md`** and **`config/preservation/submission-sources.yaml`**.

**Remote intake lanes:** **`docs/remote-intake-lanes.md`** (intake handoff → archiver; private lane inventory template in `config/preservation/lane-inventory.template.yaml`).

**Ops UI:** the **ledger** app (`ledger/`, image `ghcr.io/hitsave/ledger`) on `:8090`; production at `https://ledger.hitsave.org` behind Caddy + oauth2-proxy (Pocket ID) on luigi (ledger, health, **`/correct`**, **`/quarantine`**, publish/hide) — config in the private repo (`ledger.yaml`, from **`config/ledger.yaml.example`**). **Windmill** (schedules, job logs): **`windmill_ops/README.md`**; Windmill keeps its own copy of each script; after each merge a systemd timer on saturn (`scripts/deploy/deploy-poll.sh`) waits for green checks, then syncs Windmill, rebuilds the ingest-worker/omeka-catalog images and deploys the ledger as needed (**`docs/deploy-on-merge.md`**).

**Storage:** Wasabi is the permanent copy; local bags and `ready/` are pruned after verification — **`docs/storage-retention.md`**. Archivist-facing usage: **`docs/archivist-guide.md`**.

## Docker

Use `docker compose` (ingest worker, omeka-catalog, CI fixtures). Do not run preservation scripts with bare host Python when the stack defines the runtime.

After changing `ingest-worker/` or submission security code, run:

```bash
CI=1 ./scripts/test-submission-security-fixtures.sh
```

## Git

Ship public changes via PR when this repo uses branch protection; push private config only to **hitsave-archiver-config** (private remote).

**Do not commit** operator-specific preservation batch manifests under `config/preservation/` (real tank paths, one-off game lists, local batch names). Copy `batch.yml.example` to a local `batch.yml` or another ignored `batch-*.yml` path. Never commit `data/` from test runs.
