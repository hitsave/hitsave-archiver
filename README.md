# hitsave-archiver

Public preservation machinery for [HitSave](https://hitsave.org): press-material and portable zip ingest, E-ARK AIP/DIP packaging, ClamAV scanning, optional Wasabi AIP upload, and Omeka S DIP upload via REST.

**Credentials never belong in this repo.** Operator secrets use a separate checkout mounted at runtime; see `docs/public-repos.md`.

Related public repos:

- [`hitsave-archive-theme`](https://github.com/jonasrosland/hitsave-archive-theme) — Omeka S theme (Foundation overlay)
- [`omeka-dip-viewer`](https://github.com/jonasrosland/omeka-dip-viewer) — Omeka S DIP browse module

## Layout (two clones)

```text
~/hitsave-archiver/              # this repo (public)
~/hitsave-archiver-config/       # secrets + operator database.yaml (private)
```

Docker Compose mounts the private tree at `/config/secrets` and overlays `/config/preservation/database.yaml`. Override the host path with `HITSAVE_PRIVATE_CONFIG` if your private clone is not a sibling directory.

## Quick start (dev / CI)

```bash
git clone https://github.com/jonasrosland/hitsave-archiver.git
cd hitsave-archiver

# Placeholder secrets for local/CI (real keys go in the private repo):
./scripts/ensure-local-config.sh   # creates ../hitsave-archiver-config from *.example if missing

# Submission security fixtures (Docker):
CI=1 ./scripts/test-submission-security-fixtures.sh
```

Production operators clone **both** repos, fill in real files under `hitsave-archiver-config/secrets/`, edit `preservation/database.yaml`, then:

```bash
export HITSAVE_PRIVATE_CONFIG=/path/to/hitsave-archiver-config
export HOST_PRESS_MATERIAL=/data/press-material
export HOST_SUBMISSIONS=/data/submissions/incoming/..
export HOST_OUTPUT=/data/output
python3 scripts/sync-preservation-config.py
docker compose up -d --build
```

See `docs/portable-submissions.md`, `docs/preservation-wasabi.md`, and `docs/omeka-production.md`.

## Saturn (example paths)

| Path | Role |
|------|------|
| `/home/jonas/hitsave-archiver` | Public code checkout |
| `/home/jonas/hitsave-archiver-config` | Private secrets checkout |
| `/tank/hitsave-archiver/submissions` | Portable zip intake |
| `/tank2/press-material` | Press-material source tree |
| `/tank/hitsave-archiver/output` | AIP/DIP output |

Set `HITSAVE_PRIVATE_CONFIG` and the `HOST_*` variables in a host env file (not committed).

## Config hygiene

- Committed templates: `config/secrets/*.example`, `config/preservation/database.yaml.example`
- Before pushing changes, scan for accidental secrets:

```bash
git grep -iE 'secret_access|AKIA[0-9A-Z]{16}|BEGIN (RSA|OPENSSH)' -- ':!*.example' ':!docs/' ':!.ci/private-config/'
```
