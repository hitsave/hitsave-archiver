# Operator config — private checkout and examples

Preservation Compose mounts a **private config directory** at `/config/secrets` and overlays `/config/preservation/database.yaml`. That directory is **not** committed to this public repo. Operators keep it in a separate checkout ([**hitsave-archiver-config**](https://github.com/hitsave/hitsave-archiver-config) for Hit Save, or any path you set with `HITSAVE_PRIVATE_CONFIG`).

All **templates** to build that layout live **here**, under `config/**/*.example` (plus host path exports below).

## Automatic bootstrap

From this repo (public clone):

```bash
./scripts/ensure-local-config.sh
```

Creates a sibling `../hitsave-archiver-config` (or uses `HITSAVE_PRIVATE_CONFIG`) and copies each missing file from the matching `*.example` in the table below. Replace `REPLACE_ME` / example passwords before production use.

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
  preservation/
    database.yaml
```

## Example files in this repo (hitsave-archiver)

| File you maintain | Copy from (this repo) |
|-------------------|------------------------|
| `secrets/wasabi-credentials.yaml` | [`config/secrets/wasabi-credentials.yaml.example`](../config/secrets/wasabi-credentials.yaml.example) |
| `secrets/mobygames-credentials.yaml` | [`config/secrets/mobygames-credentials.yaml.example`](../config/secrets/mobygames-credentials.yaml.example) |
| `secrets/omeka-api-credentials-local.yaml` | [`config/secrets/omeka-api-credentials-local.yaml.example`](../config/secrets/omeka-api-credentials-local.yaml.example) |
| `secrets/omeka-api-credentials-prod.yaml` | [`config/secrets/omeka-api-credentials-prod.yaml.example`](../config/secrets/omeka-api-credentials-prod.yaml.example) |
| `preservation/database.yaml` | [`config/preservation/database.yaml.example`](../config/preservation/database.yaml.example) |
| `host.env` (shell exports for `HOST_*`) | [`config/host.env.example`](../config/host.env.example) |

Generic Omeka API template (pick local vs prod filename above): [`config/secrets/omeka-api-credentials.yaml.example`](../config/secrets/omeka-api-credentials.yaml.example).

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
cp "$ARCHIVER/config/host.env.example" "$PRIVATE/host.env"
# Edit every file; then: source "$PRIVATE/host.env"
```

## Operator YAML in the public archiver clone (gitignored)

These stay in **your hitsave-archiver working tree**, not in the private repo. Copy from examples in this repo:

| File | Example |
|------|---------|
| `config/preservation/game.yml` | [`config/preservation/game.yml.example`](../config/preservation/game.yml.example) |
| `config/preservation/batch.yml` | [`config/preservation/batch.yml.example`](../config/preservation/batch.yml.example) |

Committed policy (no secrets): [`config/omeka-uploader.yaml`](../config/omeka-uploader.yaml), [`config/preservation/ingest.yaml`](../config/preservation/ingest.yaml), [`config/preservation/submissions.yaml`](../config/preservation/submissions.yaml).

## Test Omeka (hitsave-omeka-test)

Omeka admin/database settings are separate from preservation secrets. Examples live in [**hitsave-omeka-test**](https://github.com/hitsave/hitsave-omeka-test) under `config/omeka-test/*.example`; run `./scripts/ensure-local-config.sh` there. Omeka **upload** still uses `secrets/omeka-api-credentials-local.yaml` from the private checkout above.

End-to-end DIP QA: [hitsave-omeka-test `docs/build-real-dip.md`](https://github.com/hitsave/hitsave-omeka-test/blob/main/docs/build-real-dip.md).

## See also

- [public-repos.md](./public-repos.md) — repo split and git hygiene
- [portable-submissions.md](./portable-submissions.md) — zip intake paths (`HOST_SUBMISSIONS`)
