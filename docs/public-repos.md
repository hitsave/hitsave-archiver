# Public repositories — split and secrets

## Published repos

| Repo | Visibility | Contents |
|------|------------|----------|
| [**hitsave-archiver**](https://github.com/hitsave/hitsave-archiver) | Public (Apache-2.0) | Ingest worker, BagIt scripts, Compose stack, schema, CI fixtures |
| [**hitsave-archiver-config**](https://github.com/hitsave/hitsave-archiver-config) | **Private** | Wasabi, Omeka API, MobyGames credentials; `preservation/database.yaml` |
| [**hitsave-omeka-test**](https://github.com/hitsave/hitsave-omeka-test) | Public (Apache-2.0) | Local Omeka S stack, theme/DIP QA, prod mirror scripts |
| [**hitsave-archive-theme**](https://github.com/hitsave/hitsave-archive-theme) | Public (GPL-3.0-or-later) | Foundation overlay theme build |
| [**omeka-dip-viewer**](https://github.com/hitsave/omeka-dip-viewer) | Public (Apache-2.0) | Omeka S module `OmekaDipViewer` — BagIt browse on Wasabi S3 (`omeka_dip_package`) |

## Never commit to the public archiver

Real credentials live only in **hitsave-archiver-config** (mounted at `/config/secrets` and as `preservation/database.yaml`). Templates to create that tree: **[operator-config.md](./operator-config.md)** (`config/secrets/*.example`, `config/preservation/database.yaml.example`, `config/host.env.example`). Committed policy YAML (paths, limits, upload targets): **[config-contract.md](./config-contract.md)**.

Gitignored on the public archiver clone (operator batch configs):

- `config/preservation/batch.yml`, `game.yml`, `generated/` (batch expand output)
- `config/preservation/generated.env` (rendered from private `database.yaml`)

Omeka test stack operator files live in **hitsave-omeka-test** (see that repo’s `.gitignore`).

CI materializes `.ci/private-config/` from `config/**/*.example` via `scripts/bootstrap-private-config-from-examples.sh --force` (gitignored; never live keys).

## Bootstrap

```bash
./scripts/ensure-local-config.sh
```

## Pre-push scan (public repo)

```bash
git grep -iE 'key_credential|secret_access|password.*[0-9]{6}|BEGIN (RSA|OPENSSH)|AKIA[0-9A-Z]{16}' \
  -- ':!*.example' ':!docs/'
```

Rotate credentials if they ever appeared in public git history.

## Legacy monorepo

`hitsave-archive-agent` was the pre-split monorepo checkout; **hitsave-archiver** + **hitsave-omeka-test** are the canonical public code path going forward.
