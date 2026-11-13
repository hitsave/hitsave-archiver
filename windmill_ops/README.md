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
| `sync_catalog_snapshots` | 05:30 | Adds a dated copy of the catalog record to each bag whose Omeka record changed (`metadata/catalog/`); unchanged bags are only read ([preservation-packaging-standard.md](../docs/preservation-packaging-standard.md#catalog-record-copies)). |
| `publish_damaged_files_page` | Every 3 h at :15 | Rebuilds the public "Damaged files" page from the ledger ([damaged-files.md](../docs/damaged-files.md)). |

Each schedule is defined in `wmill/f/hitsave/<name>.schedule.yaml`, next to its script. The
times are chosen so the daily jobs don't overlap: the ledger backup runs before the fixity
check, and the prune and catalog copies run after it. The prune deletes local copies only after
the first scheduled dry run on 2026-11-02 verified 50 of 50 packages; set `execute` to `'false'`
in its schedule for a read-only report.

## Scripts (`f/hitsave/`)

| Script | Role |
|---|---|
| `remote_intake_sweep` | Scheduled sweep (above) |
| `ledger_backup` | Scheduled ledger dump (above) |
| `wasabi_fixity_check` | Scheduled Wasabi fixity (above). Run with `max_packages: 0` to only count what's due. |
| `prune_local_working_copies` | Scheduled prune (above). Set `execute: 'false'` for a read-only report. |
| `correct_catalog_item` | Catalog correction. the ledger's **`/correct`** submits this job. |
| `quarantine_catalog_item` | Manual quarantine (`scripts/curation-quarantine-omeka.py`); the ledger's **`/quarantine`** offers the same form |
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

Windmill keeps **its own copy** of every script and schedule. After a merge into `main`, the
deploy container on saturn makes Windmill match `windmill_ops/wmill/`: it pushes changed scripts
and schedules, and deletes any under `f/` that git no longer has. See
[deploy-on-merge.md](../docs/deploy-on-merge.md).

The files use the layout and formatting that `wmill sync pull` writes, so a deploy pushes only
what changed. Keep it that way: write a new schedule as `f/hitsave/<name>.schedule.yaml`, and
put explanations in this README, because Windmill doesn't keep YAML comments.

To see what a deploy would change, run `./scripts/windmill-sync-push.sh --dry-run` from the
checkout on saturn. Check a cron expression without deploying it with
`POST :8089/api/schedules/preview` and a body of `{"schedule", "timezone"}`.

## Config

- Public paths and URLs: `config/windmill-ops.yaml`, `windmill_ops/docker/.env.example`, `worker-paths.env.example`.
- Secrets and host paths: `hitsave-archiver-config` (`windmill/`, `secrets/`, `intake/`).

Non-archiver automation belongs in other repos or Windmill folders, not under `f/hitsave/`.
