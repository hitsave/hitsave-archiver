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

### Manifest line format

bagit-python writes payload manifests with two spaces between checksum and path, and tag manifests with one. Both are valid BagIt. Scripts that change a manifest later (`scripts/bag_manifest.py`) write every line with the separator of the file's first line, so each manifest uses one style throughout. That matters for a restore by hand: GNU and uutils `sha256sum -c` read either style, but not both in one file. Older edits added two-space lines to one-space tag manifests; each such file becomes uniform the next time a script rewrites it, which the nightly catalog-copy job does for every bag.

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
| `metadata modification` | `apply_dvd_menu_bag_previews.py`, `refresh_bag_previews_wasabi.py`, `sync_dump_bag_metadata_from_ledger.py`, `patch-press-catalog-omeka-and-bag.py`, `sync-catalog-snapshots.py` | Each time the script changes a bag's tag files |
| `recovery` | `glacier-restore-worker.py` | After files are restored from the Glacier copy |

A script that changes a bag on Wasabi uploads the event file before the new tagmanifest, as it does with every other tag file. Shared code: `scripts/premis_events.py`.

Each change reads the tagmanifest, adds lines and writes it back. If two jobs did that to one package at the same time, the second write would drop the first job's lines. So the jobs that run without a person watching take a per-package lock in the ledger database (`scripts/package_lock.py`) before they read the tagmanifest, and hold it until their last write: `sync-catalog-snapshots.py`, `patch-press-catalog-omeka-and-bag.py` (catalog corrections from the ledger), `sync_dump_bag_metadata_from_ledger.py` and `glacier-restore-worker.py`. A job that waits more than 10 minutes for the lock stops with "another job is writing to package ...", and the scheduled jobs try again on their next run. The one-off repair and backfill scripts don't take the lock yet, so don't run them on a package while one of those jobs is writing to it. If a line does go missing, the nightly Wasabi check reports the event file as an untracked tag file.

Fixity checks of the Wasabi copy are not written into bags. They stay in the ledger's `fixity_run` table, which the nightly `pg_dump` backs up. Writing them into bags would rewrite the tagmanifest of every checked package on Wasabi every day. Archivematica does the same: its Storage Service keeps fixity results in its database, not in the AIP.


Unpacked bag trees only — one prefix per package. Press: `press-material/{sort_letter}/{package_id}/`. Dumps: see [dump-preservation.md](./dump-preservation.md).

A `package_id` is `{title_slug}-{uuid_v7}`, and the slug is ASCII only. `title_slug()` in `scripts/package_identity.py` splits accented letters into a base letter and marks and drops the marks, so "Ōkami" becomes `okami`. Letters that Unicode can't split that way go through a small table: Æ becomes AE, ß becomes ss, and Ø, Ł, Đ, Œ, Þ and ı become O, L, D, OE, Th and i. A title with no Latin letters gets the slug `game`. Folder and file names inside a bag keep their original characters.

macOS stores "ō" as "o" plus a separate macron, and Linux usually stores it as one character. The two look the same but are different bytes. The ledger keeps `source_path` exactly as it is on disk, so scripts can open it, but every lookup by `source_path` compares both sides in Unicode NFC form. The `sip_fingerprint` is also computed from the NFC form of the path. A test fails if new code compares `source_path` without NFC.

## Catalog record copies

Each bag keeps dated copies of its catalog record under `metadata/catalog/`, so the package still says what it is if the catalog is lost. Without them, a bag holds the title, type, region, format and donor, but not the description, rights statement, access wording, creator and publisher, ARK, item sets or public/hidden status. The 2026-11-10 restore test found that gap.

Each copy is one file, `catalog-<UTC time>.json`, built from the item's Omeka record by `scripts/catalog_snapshot.py`:

- `about` explains the file in plain words, so a reader doesn't need Omeka or this repository.
- `values` holds every property, keyed by its Dublin Core (`dcterms:`) or BIBO (`bibo:`) term. A link to another item carries that item's ARK and title. A value marked `public: false` is shown to staff only.
- `item_sets`, `resource_template` and `resource_class` carry names as well as ids.
- `ark`, `omeka_item_id`, `public`, `created` and `omeka_modified` identify the record. `taken_at` says when the copy was made.

Copies are only ever added. When the record changes, a new dated file goes next to the older ones, with a `metadata modification` event that names the changed fields. The newest file is the current record. Other archives work the same way: Archivematica adds an "updated" METS section and keeps the original, and OCFL and E-ARK keep earlier versions of an object.

The Windmill job `f/hitsave/sync_catalog_snapshots` runs `scripts/sync-catalog-snapshots.py --execute` daily at 05:30 UTC. It compares each package's Omeka record with the bag's newest copy and writes only to bags whose record changed, so an edit made anywhere (Omeka admin, the ledger's correction box, a script) reaches the bag within a day. Its first run is the backfill. Without `--execute`, it reports what it would add:

```bash
docker compose run --rm -T --no-deps -v "$PWD/scripts:/app/scripts:ro" \
  --entrypoint python omeka-catalog /app/scripts/sync-catalog-snapshots.py [--game-key KEY] [--max-writes N] [--execute]
```

The copies don't replace the Omeka database backups. Item set pages, site pages and the theme exist only there.

## Omeka

OmekaDipViewer loads **`metadata/inventory.json`** from the Wasabi prefix and streams files under **`data/`** via S3. Registration is API-only (`register-dip-omeka-api.py`).
