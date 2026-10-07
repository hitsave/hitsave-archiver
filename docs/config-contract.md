# Config contract — single source of truth (SSOT)

Preservation behavior reads committed YAML under `config/`. Operator copies (`game.yml`, `batch.yml`, private secrets) should **extend** these files, not restate paths or limits unless you mean to override.

## Primary SSOT files

| Topic | File | What belongs here |
|-------|------|-------------------|
| Container paths, ingest defaults, agent metadata | [`config/preservation/ingest.yaml`](../config/preservation/ingest.yaml) | `paths.*`, `defaults.max_files`, `defaults.max_total_bytes`, `preservation.repository_code`, ClamAV host/port |
| E-ARK profiles, METS, validation tools | [`config/preservation/packaging.yaml`](../config/preservation/packaging.yaml) | Profiles, schema dir, validator URLs — not runtime paths |
| Portable zip limits, allowlists | [`config/preservation/submissions.yaml`](../config/preservation/submissions.yaml) | `limits.*`, `security.*`, submission-only `preservation.video_access` |
| Omeka REST upload target | [`config/omeka-uploader.yaml`](../config/omeka-uploader.yaml) | API base URL, site slug, item set, dip ingester name |
| ClamAV scan caps (aligned with submissions) | [`config/clamav/clamd.conf`](../config/clamav/clamd.conf) | Must stay in sync with `submissions.yaml` `limits.max_member_bytes` / `max_archive_members` — see [`config/clamav/README.md`](../config/clamav/README.md) |
| Private credentials | **hitsave-archiver-config** (from `config/secrets/*.example`) | Wasabi, Omeka API, Moby, ledger DB |
| Test Omeka admin URL / site slug | **hitsave-omeka-test** `config/omeka-test/settings.yaml` | Must match `omeka-uploader.yaml` `site_slug` and API base when uploading to test |

## Game and batch operator YAML

[`scripts/preservation_common.py`](../scripts/preservation_common.py) **`resolve_game_config()`** merges operator YAML with `ingest.yaml`:

- **`source_subpath`** — folder under `paths.press_material_root` (preferred in examples).
- **`game_key`**, **`omeka_item_title`** — required for single-game ingest.
- **`batch_key`** — set on generated batch configs; output paths go under `…/batch/{batch_key}/`.
- Derived unless overridden: `source_game_folder`, `output_tar`, `staging_dir`, `aip_bag_dir`, `max_files`, `max_total_bytes`, `preservation.*`.

Examples: [`game.yml.example`](../config/preservation/game.yml.example), [`batch.yml.example`](../config/preservation/batch.yml.example).

Batch expand writes **minimal** per-game YAML under `config/preservation/generated/{batch_key}/` (`source_subpath`, `game_key`, `omeka_item_title`, `batch_key` only). Ingest resolves paths at runtime via **`resolve_game_config()`**. Batch output dirs come from **`batch_output_dirs()`** unless `batch.yml` `output:` overrides.

## Intentional duplication (documented)

| Copy | SSOT | Notes |
|------|------|--------|
| `clamd.conf` byte limits | `submissions.yaml` `limits.max_member_bytes` | ClamAV has no YAML import; tune both when changing submission caps |
| `status-web.yaml` Omeka URLs | hitsave-omeka-test `settings.yaml` | Status UI only; keep URLs consistent manually |
| `omeka-uploader.yaml` `site_slug` | test Omeka settings | Same as above |

## Anti-patterns

- Full paths like `/data/press-material/...` in new operator YAML — use **`source_subpath`**.
- Repeating `max_files` / `repository_code` in every `game.yml` — omit; defaults come from `ingest.yaml`.
- Committing `config/preservation/generated/` — gitignored; batch expand output only.

See also: [operator-config.md](./operator-config.md), [public-repos.md](./public-repos.md).
