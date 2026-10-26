# Windmill ops (hitsave-archiver)

Self-hosted [Windmill](https://www.windmill.dev/) for preservation **run**, **schedule**, and **monitor** — bash scripts in git (`windmill_ops/wmill/`) that call existing archiver scripts and `archiver_compose` (host **`ARCHIVER_HOST_PATH`**, not container `/archiver`).

## Start

```bash
cp windmill_ops/docker/.env.example windmill_ops/docker/.env
cp windmill_ops/docker/worker-paths.env.example windmill_ops/docker/worker-paths.env
# Edit both: ARCHIVER_HOST_PATH, private config, gdrive-intake host paths, WMILL_TOKEN

cd windmill_ops/docker
docker compose up -d
```

UI: **http://127.0.0.1:8089** (or host LAN). Windmill uses its own **`windmill_db`**; preservation Postgres is unchanged.

**Worker:** `windmill_worker` joins the preservation Docker network for ledger TCP and mounts the Docker socket for compose. After changing path env, recreate the worker: `docker compose up -d --no-deps windmill_worker`.

## Sync scripts from git

```bash
# WMILL_TOKEN in windmill_ops/docker/.env
./scripts/windmill-sync-push.sh
```

Uses the **CLI container** (no host `npm`): `docker compose --profile cli run --rm windmill_cli sync push …` plus schedule push for **`remote_intake_sweep`**.

## Scripts (`f/hitsave/`)

| Script | Role |
|--------|------|
| `ledger_health_report` | `python3 scripts/ledger_health_report.py` (read-only Postgres) |
| `remote_intake_sweep` | Wasabi then Drive per-folder pipeline (scheduled) |
| `wasabi_redump_inbox_sweep` / `gdrive_redump_inbox_sweep` | Single lane (manual) |
| `batch_resume_omeka` | `run-batch-resume-omeka.sh` (long; manual) |
| `correct_catalog_item` | `scripts/correct-catalog-item.py` (optional; status-web **`/correct`** is the default UI) |

**Schedule:** only **`remote_intake_sweep`** every **30 minutes**. Ops SSOT for intake state: **`game_ingest`** ledger + status-web **Health** (`:8090`).

## Config SSOT

- Paths: **`config/windmill-ops.yaml`**, **`windmill_ops/docker/.env.example`**, **`worker-paths.env.example`**
- Secrets: **`hitsave-archiver-config`** (mounted at `/config/private` on the worker)

Non-archiver automation (e.g. a future Discord bot) should live in other repos or Windmill folders — not under `f/hitsave/` here.
