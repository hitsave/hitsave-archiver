# hitsave-archiver

Public preservation machinery for [HitSave](https://hitsave.org): press-material and portable zip ingest, E-ARK AIP/DIP packaging, ClamAV scanning, optional Wasabi AIP upload, and Omeka S DIP upload via REST.

**Credentials never belong in this repo.** Operator secrets use a separate checkout mounted at runtime; build it from `config/**/*.example` — see **[docs/operator-config.md](docs/operator-config.md)**, **[docs/config-contract.md](docs/config-contract.md)**, and [docs/public-repos.md](docs/public-repos.md).

Related public repos:

- [`hitsave-omeka-test`](https://github.com/hitsave/hitsave-omeka-test) — local Omeka S QA stack (theme, DipViewer, prod mirror)
- [`hitsave-archive-theme`](https://github.com/hitsave/hitsave-archive-theme) — Omeka S theme (Foundation overlay)
- [`omeka-dip-viewer`](https://github.com/hitsave/omeka-dip-viewer) — Omeka S DIP browse module

## Layout (three clones)

```text
~/hitsave-archiver/              # this repo (public)
~/hitsave-archiver-config/       # secrets + operator database.yaml (private)
~/hitsave-omeka-test/            # local Omeka on :8088 (optional, for QA / uploader target)
```

Docker Compose mounts the private tree at `/config/secrets` and overlays `/config/preservation/database.yaml`. Override the host path with `HITSAVE_PRIVATE_CONFIG` if your private clone is not a sibling directory.

Omeka REST upload settings: `config/omeka-uploader.yaml` (points at test or prod API via private credentials).

**Multi-game E2E (batch ingest + Omeka):** [docs/e2e-preservation-batch.md](docs/e2e-preservation-batch.md).

## Quick start (dev / CI)

```bash
git clone https://github.com/hitsave/hitsave-archiver.git
cd hitsave-archiver

# Bootstrap private config from config/secrets/*.example and database.yaml.example:
./scripts/ensure-local-config.sh   # see docs/operator-config.md for the full file list

# Submission security fixtures (Docker):
CI=1 ./scripts/test-submission-security-fixtures.sh
```

Production operators clone **archiver + config**, fill in real files under `hitsave-archiver-config/secrets/`, edit `preservation/database.yaml`, then:

```bash
export HITSAVE_PRIVATE_CONFIG=/path/to/hitsave-archiver-config
export HOST_PRESS_MATERIAL=/data/press-material
export HOST_SUBMISSIONS=/data/submissions/incoming/..
export HOST_OUTPUT=/data/output
python3 scripts/sync-preservation-config.py
docker compose up -d --build
```

For a local Omeka target, also clone **hitsave-omeka-test** and set `HITSAVE_OMEKA_TEST_ROOT` when using `scripts/run-fresh-test-stack.sh`.

See `docs/portable-submissions.md` and `docs/preservation-wasabi.md`.

## Example host paths

| Path | Role |
|------|------|
| `~/hitsave-archiver` | Public code checkout |
| `~/hitsave-archiver-config` | Private secrets checkout |
| `~/hitsave-omeka-test` | Local Omeka test stack |
| `/tank/hitsave-archiver/submissions` | Portable zip intake |
| `/tank2/press-material` | Press-material source tree |
| `/tank/hitsave-archiver/output` | AIP/DIP output |

Set `HITSAVE_PRIVATE_CONFIG` and the `HOST_*` variables in a host env file (not committed).

## Config hygiene

- Committed templates: `config/secrets/*.example`, `config/preservation/database.yaml.example`, `config/omeka-uploader.yaml`
- Before pushing changes, scan for accidental secrets:

```bash
git grep -iE 'secret_access|AKIA[0-9A-Z]{16}|BEGIN (RSA|OPENSSH)' -- ':!*.example' ':!docs/' ':!.ci/private-config/'
```
