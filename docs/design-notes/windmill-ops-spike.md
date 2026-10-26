# Windmill ops (preservation host)

Self-hosted [Windmill](https://www.windmill.dev/) beside the preservation stack on **Saturn** (dev clone under `hitsave-e2e`). Bash scripts in **`windmill_ops/wmill/`** wrap existing `scripts/` and **`archiver_compose`** — no duplicated ingest logic.

## Layout

- **`windmill_ops/docker/`** — Windmill server, DB, hitsave worker (Docker socket + host path mounts)
- **`windmill_ops/wmill/`** — `wmill sync` tree + **`remote_intake_sweep`** schedule (30 minutes)
- **`config/windmill-ops.yaml`** — public paths/URLs only (no secrets)

## Operator setup

1. `cp windmill_ops/docker/.env.example windmill_ops/docker/.env` — set **`ARCHIVER_HOST_PATH`**, **`HITSAVE_PRIVATE_CONFIG_HOST`**, **`HITSAVE_GDRIVE_INTAKE_HOST`**, **`WMILL_TOKEN`**
2. `cp windmill_ops/docker/worker-paths.env.example windmill_ops/docker/worker-paths.env` — same **`ARCHIVER_HOST_PATH`** (gitignored on disk; never commit)
3. Archiver checkout: `./scripts/ensure-local-config.sh` (needs `config/preservation/generated.env`)
4. `cd windmill_ops/docker && docker compose up -d`
5. `./scripts/windmill-sync-push.sh`

Set **concurrency limit 1** on **`remote_intake_sweep`** in the Windmill UI.

Detail: **`windmill_ops/README.md`**.
