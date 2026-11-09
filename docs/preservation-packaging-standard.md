# Hit Save packaging standard

Hit Save preserves press material and dumps as **RFC 8493 BagIt** packages with payload under **`data/`**, fixity via **`manifest-sha256.txt`**, and sidecar metadata under **`metadata/`** (`submission.json`, `inventory.json`, optional Hit Save preview manifest).

Machine-readable settings: **`config/preservation/packaging.yaml`** (BagIt version, payload dir, Wasabi unpacked key pattern, validation).

Full OAIS mapping, pipeline, and Omeka access: **[oais-bagit-preservation-plan.md](./oais-bagit-preservation-plan.md)**.

## Validation

| Check | Tool |
|-------|------|
| Bag structure + manifests | `bagit-python` (`bag.validate()`) |
| Damaged files | `file_integrity.py` at build; `check-integrity-wasabi.py` for older packages ([damaged-files.md](./damaged-files.md)) |
| Ingest gate | `build-bagit-package.py` after `make_bag` |
| Periodic fixity | `wasabi-fixity-check.py` on the Wasabi copy, every package at most every 90 days ([storage-retention.md](./storage-retention.md#fixity-schedule-wasabi-copy)); `fixity-check.py` for local bags |

## PREMIS events

Each bag records what happened to it as PREMIS 3 events, one XML file per event under `metadata/premis/`. Event types come from the [Library of Congress PREMIS vocabulary](https://id.loc.gov/vocabulary/preservation/eventType.html). Each file is named `<slug>-<UTC time>-<first 8 characters of the event id>.xml` and is listed in `tagmanifest-sha256.txt`.

Events are only ever added. A later change adds a new event file and one tagmanifest line, and never rewrites an earlier event.

| Event | Written by | When |
|-------|-----------|------|
| `ingestion` | `build-bagit-package.py` | Every new bag |
| `message digest calculation` | `build-bagit-package.py` | Every new bag |
| `format identification` | `build-bagit-package.py` | Every new bag. The outcome is `failure` if Siegfried didn't run |
| `validation` | `build-bagit-package.py`, `check-integrity-wasabi.py` | When a bag has images, PDFs, video/audio or archives. The outcome is `fail` if any is damaged |
| `virus check` | `ingest-folder.py`, `rescan-virus-unscanned.py`, `add_catalogue_scans_wasabi.py` | Before upload, and on a rescan |
| `metadata modification` | `apply_dvd_menu_bag_previews.py`, `refresh_bag_previews_wasabi.py`, `sync_dump_bag_metadata_from_ledger.py`, `patch-press-catalog-omeka-and-bag.py` | Each time the script changes a bag's tag files |

A script that changes a bag on Wasabi uploads the event file before the new tagmanifest, as it does with every other tag file. Shared code: `scripts/premis_events.py`.

Fixity checks of the Wasabi copy are not written into bags. They stay in the ledger's `fixity_run` table, which the nightly `pg_dump` backs up. Writing them into bags would rewrite the tagmanifest of every checked package on Wasabi every day. Archivematica does the same: its Storage Service keeps fixity results in its database, not in the AIP.


Unpacked bag trees only — one prefix per package. Press: `press-material/{sort_letter}/{package_id}/`. Dumps: see [dump-preservation.md](./dump-preservation.md).

## Omeka

OmekaDipViewer loads **`metadata/inventory.json`** from the Wasabi prefix and streams files under **`data/`** via S3. Registration is API-only (`register-dip-omeka-api.py`).
