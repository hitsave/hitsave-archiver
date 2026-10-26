# Operator config — private checkout and examples

Config SSOT map (paths, defaults, which file owns what): **[config-contract.md](./config-contract.md)**.

Preservation Compose mounts a **private config directory** at `/config/secrets` and overlays `/config/preservation/database.yaml`. That directory is **not** committed to this public repo. Operators keep it in a separate checkout ([**hitsave-archiver-config**](https://github.com/hitsave/hitsave-archiver-config) for Hit Save, or any path you set with `HITSAVE_PRIVATE_CONFIG`).

All **templates** to build that layout live **here**, under `config/**/*.example` (plus host path exports below).

## Automatic bootstrap

From this repo (public clone):

```bash
./scripts/ensure-local-config.sh
```

Creates a sibling `../hitsave-archiver-config` (or uses `HITSAVE_PRIVATE_CONFIG`) and copies each missing file from the matching `*.example` in the table below. Replace `REPLACE_ME` / example passwords before production use.

**CI and laptop Compose overrides** (`docker-compose.ci.yml`) use the same templates: `./scripts/bootstrap-private-config-from-examples.sh --force` writes gitignored `.ci/private-config/` before tests (no duplicate committed copies).

Then:

```bash
python3 scripts/sync-preservation-config.py
```

## Private checkout layout

Target tree (your private git repo or local directory):

```text
hitsave-archiver-config/
  host.env                    # optional; not created by ensure-local-config.sh
  secrets/
    wasabi-credentials.yaml
    mobygames-credentials.yaml
    omeka-api-credentials-local.yaml
    omeka-api-credentials-prod.yaml
    windmill-status-web.token   # Windmill API token for status-web /ops job list (mode 644)
  preservation/
    database.yaml
    lane-inventory.yaml       # optional; Drive IDs / Wasabi prefixes — not in git
    wasabi-redump-intake.yaml # optional; Wasabi incoming roots for Redump
```

## Example files in this repo (hitsave-archiver)

| File you maintain | Copy from (this repo) |
|-------------------|------------------------|
| `secrets/wasabi-credentials.yaml` | [`config/secrets/wasabi-credentials.yaml.example`](../config/secrets/wasabi-credentials.yaml.example) |
| `secrets/mobygames-credentials.yaml` | [`config/secrets/mobygames-credentials.yaml.example`](../config/secrets/mobygames-credentials.yaml.example) |
| `secrets/omeka-api-credentials-local.yaml` | [`config/secrets/omeka-api-credentials-local.yaml.example`](../config/secrets/omeka-api-credentials-local.yaml.example) |
| `secrets/omeka-api-credentials-prod.yaml` | [`config/secrets/omeka-api-credentials-prod.yaml.example`](../config/secrets/omeka-api-credentials-prod.yaml.example) |
| `secrets/windmill-status-web.token` | [`config/secrets/windmill-status-web.token.example`](../config/secrets/windmill-status-web.token.example) — Windmill UI → Settings → Account → Tokens; one line, no quotes |
| `preservation/database.yaml` | [`config/preservation/database.yaml.example`](../config/preservation/database.yaml.example) |
| `preservation/lane-inventory.yaml` | [`config/preservation/lane-inventory.template.yaml`](../config/preservation/lane-inventory.template.yaml) |
| `preservation/wasabi-redump-intake.yaml` | [`config/preservation/wasabi-redump-intake.yaml.example`](../config/preservation/wasabi-redump-intake.yaml.example) |
| `host.env` (shell exports for `HOST_*`) | [`config/host.env.example`](../config/host.env.example) |

Use **local** credentials for test Omeka: copy `hitsave-archiver-config/omeka-catalog.test.yaml` over `omeka-catalog.yaml` (Compose mounts the private file at `/config/omeka-catalog.yaml`). **Production** batches and `register-dump-omeka.sh` use `omeka-catalog.yaml` → `secrets/omeka-api-credentials-prod.yaml` → `https://archive.hitsave.org/api`.

Manual copy example:

```bash
PRIVATE=~/hitsave-archiver-config
ARCHIVER=~/hitsave-archiver
mkdir -p "$PRIVATE/secrets" "$PRIVATE/preservation"
cp "$ARCHIVER/config/secrets/wasabi-credentials.yaml.example" "$PRIVATE/secrets/wasabi-credentials.yaml"
cp "$ARCHIVER/config/secrets/mobygames-credentials.yaml.example" "$PRIVATE/secrets/mobygames-credentials.yaml"
cp "$ARCHIVER/config/secrets/omeka-api-credentials-local.yaml.example" "$PRIVATE/secrets/omeka-api-credentials-local.yaml"
cp "$ARCHIVER/config/secrets/omeka-api-credentials-prod.yaml.example" "$PRIVATE/secrets/omeka-api-credentials-prod.yaml"
cp "$ARCHIVER/config/preservation/database.yaml.example" "$PRIVATE/preservation/database.yaml"
cp "$ARCHIVER/config/preservation/wasabi-redump-intake.yaml.example" "$PRIVATE/preservation/wasabi-redump-intake.yaml"
cp "$ARCHIVER/config/host.env.example" "$PRIVATE/host.env"
# Edit every file; then: source "$PRIVATE/host.env"
```

## Operator YAML in the public archiver clone (gitignored)

These stay in **your hitsave-archiver working tree**, not in the private repo. Copy from examples in this repo:

| File | Example |
|------|---------|
| `config/preservation/game.yml` | [`config/preservation/game.yml.example`](../config/preservation/game.yml.example) |
| `config/preservation/batch.yml` | [`config/preservation/batch.yml.example`](../config/preservation/batch.yml.example) — `source_subpath` is relative to [`ingest.yaml`](../config/preservation/ingest.yaml) `paths.press_material_root`; expand via [`batch-expand-preservation.sh`](../scripts/batch-expand-preservation.sh) |
| `config/status-web.yaml` | [`config/status-web.yaml.example`](../config/status-web.yaml.example) — set `ops.windmill_ui_url` on hosts that run Windmill |

Committed policy (no secrets): [`config/omeka-catalog.yaml`](../config/omeka-catalog.yaml), [`config/preservation/ingest.yaml`](../config/preservation/ingest.yaml), [`config/preservation/submissions.yaml`](../config/preservation/submissions.yaml).

## Test Omeka (hitsave-omeka-test)

Omeka admin/database settings are separate from preservation secrets. Examples live in [**hitsave-omeka-test**](https://github.com/hitsave/hitsave-omeka-test) under `config/omeka-test/*.example`; run `./scripts/ensure-local-config.sh` there. Omeka **upload** still uses `secrets/omeka-api-credentials-local.yaml` from the private checkout above.

End-to-end DIP QA: [hitsave-omeka-test `docs/build-real-dip.md`](https://github.com/hitsave/hitsave-omeka-test/blob/main/docs/build-real-dip.md).

## See also

- [remote-intake-lanes.md](./remote-intake-lanes.md) — lane SSOT and private inventory
- [public-repos.md](./public-repos.md) — repo split and git hygiene
- [portable-submissions.md](./portable-submissions.md) — zip intake paths (`HOST_SUBMISSIONS`)
