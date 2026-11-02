# hitsave-archiver

Public preservation machinery for [HitSave](https://hitsave.org). It turns contributor material
into RFC 8493 BagIt packages:

1. Scan with ClamAV.
2. Package as BagIt.
3. Upload the unpacked bag to Wasabi, the permanent copy.
4. Record the package in a Postgres ledger.
5. Catalog it in Omeka S at [archive.hitsave.org](https://archive.hitsave.org).

**Archivists start here: [docs/archivist-guide.md](docs/archivist-guide.md).** It covers how to add
material, follow its progress, correct catalog records, and what's in each package.

**Credentials never belong in this repo.** They live in the private `hitsave-archiver-config`
checkout, mounted at runtime. See [docs/operator-config.md](docs/operator-config.md).

## How it runs

| Part | What it does | Where |
|---|---|---|
| **Remote intake** | Every 30 minutes, picks up community dumps (Redump, No-Intro, …) from the Google Drive inbox and the Wasabi incoming bucket. It verifies their checksums, then ingests and catalogs them. | Windmill `remote_intake_sweep` → [hitsave-intake](https://github.com/hitsave/hitsave-intake) + `ingest-worker` |
| **Press-material batches** | Ingests game folders from the on-site press-material tree, started by an operator | [docs/e2e-preservation-batch.md](docs/e2e-preservation-batch.md) |
| **status-web** (`:8090`) | Ledger browser, health page, and catalog correction, quarantine and publish/hide | `status-web/` |
| **Windmill** (`:8089`) | Schedules and job logs: intake sweep, nightly ledger backup, Wasabi fixity, pruning of local working copies | [windmill_ops/README.md](windmill_ops/README.md) |
| **Ledger** | Postgres `game_ingest` table, one row per package; dumped nightly | `docker-compose.yml` `postgres` |

Wasabi holds the permanent copy. Local bags and intake `ready/` folders are temporary, and they're
deleted once their Wasabi copy is verified. See [docs/storage-retention.md](docs/storage-retention.md).

## Repositories

```text
hitsave-archiver/          # this repo (public): pipeline, compose stack, status-web, Windmill jobs
hitsave-archiver-config/   # private: credentials, host paths, intake / status-web / Windmill config
hitsave-intake/            # private: Drive and Wasabi inbox polling, download, checksum verification
```

Compose expects the config checkout next to this repo (`../hitsave-archiver-config`). Set
`HITSAVE_PRIVATE_CONFIG` if it lives elsewhere.

Related public repos:

- [`hitsave-omeka-test`](https://github.com/hitsave/hitsave-omeka-test): local Omeka S QA stack
- [`hitsave-archive-theme`](https://github.com/hitsave/hitsave-archive-theme): Omeka S theme
- [`omeka-dip-viewer`](https://github.com/hitsave/omeka-dip-viewer): Omeka S module that browses bags on Wasabi

## Quick start

Development and CI:

```bash
git clone https://github.com/hitsave/hitsave-archiver.git && cd hitsave-archiver
./scripts/ensure-local-config.sh                    # private config from config/**/*.example
CI=1 ./scripts/test-submission-security-fixtures.sh # submission security fixtures (Docker)
```

On a production host, with real files in `hitsave-archiver-config`:

```bash
source ../hitsave-archiver-config/host.env    # HOST_* paths and HITSAVE_PRIVATE_CONFIG
python3 scripts/sync-preservation-config.py   # writes config/preservation/generated.env
docker compose up -d postgres clamav status-web
(cd windmill_ops/docker && docker compose up -d)   # see windmill_ops/README.md
```

## Documentation

| Read this | For |
|---|---|
| [archivist-guide.md](docs/archivist-guide.md) | Adding material, monitoring, corrections, what's in a package |
| [remote-intake-lanes.md](docs/remote-intake-lanes.md) | How Drive and Wasabi intake works end to end |
| [storage-retention.md](docs/storage-retention.md) | Which copies are permanent, fixity schedule, pruning |
| [oais-bagit-preservation-plan.md](docs/oais-bagit-preservation-plan.md) | OAIS roles, BagIt layout, Wasabi keys, Omeka access |
| [dump-preservation.md](docs/dump-preservation.md) | Dump classification (Redump, No-Intro) and Wasabi layout |
| [e2e-preservation-batch.md](docs/e2e-preservation-batch.md) | Press-material batches |
| [portable-submissions.md](docs/portable-submissions.md) | Portable zip submissions and their security checks |
| [operator-config.md](docs/operator-config.md), [config-contract.md](docs/config-contract.md) | Private config files and which YAML owns what |
| [windmill_ops/README.md](windmill_ops/README.md) | Windmill setup, jobs and schedules |

## Config hygiene

Before pushing, scan for accidental secrets:

```bash
git grep -iE 'secret_access|AKIA[0-9A-Z]{16}|BEGIN (RSA|OPENSSH)' -- ':!*.example' ':!docs/'
```

## License

Copyright 2026 Hit Save! Licensed under the [Apache License, Version 2.0](LICENSE). You may fork and
modify this project without an obligation to contribute changes back.
