# Config contract — single source of truth (SSOT)

Preservation behavior reads committed YAML under `config/`. Operator copies (`game.yml`, `batch.yml`, private secrets) should **extend** these files, not restate paths or limits unless you mean to override.

## Primary SSOT files

| Topic | File | What belongs here |
|-------|------|-------------------|
| Container paths, ingest defaults, agent metadata | [`config/preservation/ingest.yaml`](../config/preservation/ingest.yaml) | `paths.*`, `defaults.max_files`, `content_policy.*` (booru blockers, `enforce`), ClamAV host/port |
| Ingest kind → catalog routing | [`config/preservation/ingest-kinds.yaml`](../config/preservation/ingest-kinds.yaml) | `press_material`, `dump`, `other`; Wasabi unpacked prefixes — see [`dump-preservation.md`](./dump-preservation.md) |
| BagIt packaging | [`config/preservation/packaging.yaml`](../config/preservation/packaging.yaml) | BagIt dirs, Wasabi unpacked key pattern, validation |
| Portable zip limits, allowlists | [`config/preservation/submissions.yaml`](../config/preservation/submissions.yaml) | `limits.*`, `security.*`, submission-only `preservation.video_access` |
| Omeka catalog registration target | [`config/omeka-catalog.yaml`](../config/omeka-catalog.yaml) | API base URL, site slug, `external_dip`, `aip_wasabi` staff URI (`is_public: false`) |
| Service host paths (shell helpers) | [`config/preservation/host-paths.yaml`](../config/preservation/host-paths.yaml) | `submissions_root`, `output_root` — collection paths stay in `ingest.yaml` / `host.env` `HOST_*` |
| MobyGames API + public URLs | [`config/mobygames.yaml`](../config/mobygames.yaml) | `api_base`, `public_game_url_template`, ad blurb fetch, `block_on_failure` |
| ClamAV scan caps (aligned with submissions) | [`config/clamav/clamd.conf`](../config/clamav/clamd.conf) | Must stay in sync with `submissions.yaml` `limits.max_member_bytes` / `max_archive_members` — see [`config/clamav/README.md`](../config/clamav/README.md) |
| Private credentials | **hitsave-archiver-config** (from `config/secrets/*.example`) | Wasabi, Omeka API, Moby, ledger DB |
| Test Omeka admin URL / site slug | **hitsave-omeka-test** `config/omeka-test/settings.yaml` | Must match `omeka-catalog.yaml` `site_slug` and API base when uploading to test |

## Game and batch operator YAML

[`scripts/preservation_common.py`](../scripts/preservation_common.py) **`resolve_game_config()`** merges operator YAML with `ingest.yaml`:

- **`source_subpath`** — folder under `paths.press_material_root` (preferred in examples).
- **`game_key`**, **`omeka_item_title`** — required for single-game ingest.
- **`ingest_kind`** — **required** on every game YAML and on `batch.yml` for expand (see `ingest-kinds.yaml`).
- **`batch_key`** — set on generated batch configs; output paths go under `…/batch/{batch_key}/`.
- Derived unless overridden: `source_game_folder`, `bagit_bag_dir`, `staging_dir`, `aip_bag_dir`, `max_files`, `max_total_bytes`, `preservation.*`.

Examples: [`game.yml.example`](../config/preservation/game.yml.example), [`batch.yml.example`](../config/preservation/batch.yml.example).

Batch expand writes **minimal** per-game YAML under `config/preservation/generated/{batch_key}/` (`source_subpath`, `game_key`, `omeka_item_title`, `batch_key` only). Ingest resolves paths at runtime via **`resolve_game_config()`**. Batch output dirs come from **`batch_output_dirs()`** unless `batch.yml` `output:` overrides (`bag_dir`, `aip_dir`, `staging_dir`) — that override is intentional for one-off runs, not a script fallback.

## Intentional duplication (documented)

| Copy | SSOT | Notes |
|------|------|--------|
| `clamd.conf` byte limits | `submissions.yaml` `limits.max_member_bytes` | ClamAV has no YAML import; tune both when changing submission caps |
| `status-web.yaml` `omeka.public_url` / `site_slug` | hitsave-omeka-test `settings.yaml` `public_url` | Status UI Omeka links (LAN, not localhost) |
| `omeka-catalog.yaml` `site_slug` | test Omeka settings | Same as above |

## Anti-patterns

- Python or shell **fallback defaults** for URLs, buckets, or paths — scripts must read YAML and fail if a required key is missing.
- Full paths like `/data/press-material/...` in new operator YAML — use **`source_subpath`**.
- Repeating `max_files` / `repository_code` in every `game.yml` — omit; defaults come from `ingest.yaml`.
- Committing `config/preservation/generated/` — gitignored; batch expand output only.

See also: [operator-config.md](./operator-config.md), [public-repos.md](./public-repos.md).
