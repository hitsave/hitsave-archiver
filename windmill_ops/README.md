# Windmill ops (hitsave-archiver)

Self-hosted [Windmill](https://www.windmill.dev/) runs, schedules and logs the preservation jobs.
Each job is a bash script in git (`windmill_ops/wmill/f/hitsave/`). The scripts call existing
archiver scripts through `archiver_compose`, using the host path `ARCHIVER_HOST_PATH`, not the
container path `/archiver`.

## Schedules

| Schedule | When (UTC) | What it does |
|---|---|---|
| `remote_intake_sweep` | Every 30 min | Wasabi incoming, then Google Drive: stage, verify checksums, ingest, register in Omeka. Holds the remote-intake lock. |
| `ledger_backup` | 02:30 | `pg_dump` of the ledger into `HOST_LEDGER_BACKUPS`, checked with `pg_restore --list`; keeps the newest 30. |
| `wasabi_fixity_check` | 03:00 | Re-reads packages from Wasabi and verifies every SHA-256. Each package at most every 90 days, up to 300 packages / 150 GB a night ([storage-retention.md](../docs/storage-retention.md#fixity-schedule-wasabi-copy)). |
| `prune_local_working_copies` | 04:30 | Deletes local bags and `ready/` payloads proven identical on Wasabi (`execute: true`). Holds the remote-intake lock. |

The schedule definitions live in `wmill/schedules/*.schedule.yaml`. There is one Windmill worker
and it runs one job at a time, so a long job delays the next scheduled one.

## Scripts (`f/hitsave/`)

| Script | Role |
|---|---|
| `remote_intake_sweep` | Scheduled sweep (above) |
| `wasabi_redump_inbox_sweep`, `gdrive_redump_inbox_sweep` | One lane only (manual) |
| `ledger_backup` | Scheduled ledger dump (above) |
| `wasabi_fixity_check` | Scheduled Wasabi fixity (above). Run with `max_packages: 0` to only count what's due. |
| `prune_local_working_copies` | Scheduled prune (above). Set `execute: 'false'` for a read-only report. |
| `ledger_health_report` | Read-only ledger summary (`scripts/ledger_health_report.py`) |
| `correct_catalog_item` | Catalog correction. status-web **`/correct`** submits this job. |
| `quarantine_catalog_item` | Manual quarantine (`scripts/curation-quarantine-omeka.py`); status-web **`/quarantine`** offers the same form |
| `batch_resume_omeka` | `run-batch-resume-omeka.sh` (long; manual) |

Bash scripts receive their arguments **positionally**, in schema order (`x="$1"`). The worker runs
with `FAVOR_UNSHARE_PID: "false"` because PID-namespace mode drops bash arguments.

## Set up

Both env files live in the private config repo, under `hitsave-archiver-config/windmill/`:

- `.env`: compose variables (Windmill DB password, `WMILL_TOKEN`, host paths)
- `worker-paths.env`: host paths for jobs; compose mounts it from there

Compose only loads `.env` from its own folder, so link it in:

```bash
PRIVATE=../hitsave-archiver-config   # relative to the hitsave-archiver checkout
# New host without windmill/ yet: ./scripts/windmill-ops-up.sh creates both from the examples.
ln -s "$(cd "$PRIVATE" && pwd)/windmill/.env" windmill_ops/docker/.env
# Edit $PRIVATE/windmill/.env and worker-paths.env: ARCHIVER_HOST_PATH, HITSAVE_PRIVATE_CONFIG_HOST,
# HITSAVE_INTAKE_HOST, WMILL_TOKEN

mkdir -p .generated/windmill && chmod 1777 .generated/windmill   # job logs and the intake lock
cd windmill_ops/docker && docker compose up -d
```

Start the preservation stack first: Windmill joins its `hitsave-preservation_default` network.
After changing path env, recreate only the worker: `docker compose up -d --no-deps windmill_worker`.

**Paths seen from jobs.** Set `HITSAVE_PRIVATE_CONFIG_HOST` in `worker-paths.env` to the same
path as in `.env`. The worker mounts `<private>/intake` at that same host path, so remote intake
finds its config there. Never set `HITSAVE_PRIVATE_CONFIG=/config/private` on the worker: the
host Docker daemon would create bogus `/config/private/...` paths.

## Deploy script changes

Windmill keeps **its own copy** of every script and schedule. A change in git does nothing until
you push it. Push only what changed:

```bash
cd windmill_ops/docker && set -a && . ./.env && set +a
A=(--base-url http://windmill_server:8000 --token "$WMILL_TOKEN" --workspace hitsave-archiver)
docker compose --profile cli run --rm -T windmill_cli script push f/hitsave/<name>.sh "${A[@]}"
docker compose --profile cli run --rm -T windmill_cli schedule push schedules/<name>.schedule.yaml f/hitsave/<name> "${A[@]}"
```

`./scripts/windmill-sync-push.sh` pushes everything at once. Check a cron expression without
deploying it with `POST :8089/api/schedules/preview` and a body of `{"schedule", "timezone"}`.

## Config

- Public paths and URLs: `config/windmill-ops.yaml`, `windmill_ops/docker/.env.example`, `worker-paths.env.example`.
- Secrets and host paths: `hitsave-archiver-config` (`windmill/`, `secrets/`, `intake/`).

Non-archiver automation belongs in other repos or Windmill folders, not under `f/hitsave/`.
