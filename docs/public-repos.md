# Public repositories — split and secrets

## Published repos

| Repo | Visibility | Contents |
|------|------------|----------|
| [**hitsave-archiver**](https://github.com/hitsave/hitsave-archiver) | Public | Ingest worker, E-ARK scripts, Compose stack, schema, CI fixtures |
| [**hitsave-archiver-config**](https://github.com/hitsave/hitsave-archiver-config) | **Private** | Wasabi, Omeka API, MobyGames credentials; `preservation/database.yaml` |
| [**hitsave-omeka-test**](https://github.com/hitsave/hitsave-omeka-test) | Public | Local Omeka S stack, theme/DIP QA, prod mirror scripts, fixture DIPs |
| [**hitsave-archive-theme**](https://github.com/hitsave/hitsave-archive-theme) | Public | Foundation overlay theme build |
| [**omeka-dip-viewer**](https://github.com/hitsave/omeka-dip-viewer) | Public | Omeka S module `OmekaDipViewer` — E-ARK DIP `.tar` browse (`omeka_dip_package`) |

## Never commit to the public archiver

Real credentials live only in **hitsave-archiver-config** (mounted at `/config/secrets` and as `preservation/database.yaml`). The public repo keeps `*.example` templates under `config/secrets/` and `config/preservation/database.yaml.example`.

Gitignored on the public archiver clone (operator batch configs):

- `config/preservation/batch.yml`, `game.yml`, `generated/` (batch expand output)
- `config/preservation/generated.env` (rendered from private `database.yaml`)

Omeka test stack operator files live in **hitsave-omeka-test** (see that repo’s `.gitignore`).

CI uses committed placeholders under `.ci/private-config/` (no live keys).

## Bootstrap

```bash
./scripts/ensure-local-config.sh
```

## Pre-push scan (public repo)

```bash
git grep -iE 'key_credential|secret_access|password.*[0-9]{6}|BEGIN (RSA|OPENSSH)|AKIA[0-9A-Z]{16}' \
  -- ':!*.example' ':!docs/' ':!.ci/private-config/'
```

Rotate credentials if they ever appeared in public git history.

## Legacy monorepo

`hitsave-archive-agent` on Saturn was the pre-split working tree; **hitsave-archiver** + **hitsave-omeka-test** are the canonical public code path going forward.
