# Dump preservation (BagIt unpacked on Wasabi)

Ingest kind **`dump`** covers preserved file dumps: Redump disc images (`.bin`/`.cue`/`.iso`), ROM sets, PC install trees, coverdiscs, and anything else that is not community **press material** (unpacked bags under `press-material/`).

Configuration SSOT: [`config/preservation/ingest-kinds.yaml`](../config/preservation/ingest-kinds.yaml).

## Ingest kind

Set on every dump game YAML or submission handoff:

```yaml
ingest_kind: dump
```

Legacy aliases (`game_dump`, `disc_dump`, `redump`) normalize to **`dump`**.

| Kind | Wasabi layout | Moby | Redump |
|------|---------------|------|--------|
| `press_material` | `press-material/{sort_letter}/{package_id}/` (unpacked bag) | yes (`press_material`) | no |
| `dump` | unpacked prefix (below) | no (opt-in via `moby_enrich: true`) | when sidecar + SHA-1 |
| `other` | same as press (unpacked bag) | no | no |

## Wasabi prefix layout

Root bucket prefix: **`dumps/`** (config `wasabi_root: dumps`).

### Redump-class dumps

Detected automatically when the SIP contains a Redump **`!submissionInfo.txt`** / **`submissionInfo.txt`** that parses with:

- **Common Disc Info** (tab-indented fields)
- **Size & Checksum** with **SHA-1**
- **`System`** and **`Category`** fields (same labels as [redump.info](https://redump.info))

Storage path (system first, then category):

```text
dumps/{System}/{Category}/{sort_letter}/{package_id}/
  bagit.txt
  manifest-sha256.txt
  bag-info.txt
  metadata/
    submission.json
    inventory.json
  data/
    … disc images and sidecars …
```

Examples:

- `dumps/DVD-Video/Coverdiscs/F/{package_id}/` — coverdisc such as [disc 91919](https://redump.info/disc/91919)
- `dumps/PlayStation/Games/T/{package_id}/` — e.g. [disc 134898](https://redump.info/disc/134898)

Path segments use Redump **System** and **Category** strings with `/` and `\` replaced by `-`. **`package_id`** is the same `{title_slug}-{uuid_v7}` identity as press ingest.

Staff URI (Omeka / ledger): `s3://hitsave-dumps/dumps/PlayStation/Games/{package_id}/`

### No-Intro-class dumps

Detected when the SIP contains a **dumping.guide** / No-Intro text sidecar (see [Gathering and Submitting Dump Info](https://dumping.guide/submission/general)) that parses with **`Game name`**, **`--ROM(s)/file(s)--`**, and per-ROM **Size / CRC32 / MD5 / SHA-1** (SHA-256 when present). Sidecar filenames (configurable): `dumpinfo.txt`, `!dumpinfo.txt`, `no-intro.txt`, …

Required **`System:`** — use the **DAT-o-MATIC** dat label exactly (browse systems at [datomatic.no-intro.org](https://datomatic.no-intro.org/); each record page shows the heading, e.g. [Nintendo - Nintendo 3DS](https://datomatic.no-intro.org/index.php?page=show_record&s=64&n=0342)). dumping.guide **`Platform:`** is accepted as a fallback alias.

Optional: **`No-Intro record:`** (DAT-o-MATIC index number), dumper/tool/date lines from the [common template](https://dumping.guide/submission/general).

```text
dumps/No-Intro/{System}/{sort_letter}/{package_id}/
```

Example: `dumps/No-Intro/Nintendo - Nintendo 64/F/{package_id}/`

Ledger: `dump_class = no-intro`, `dump_system` = DAT-o-MATIC system label, `dump_category = ROM`.

Intake verifies each ROM file against the sidecar hashes before `ready/` (same pipeline as Redump).

### Other dumps

SIPs without a valid Redump or No-Intro sidecar are stored until dedicated automation exists:

```text
dumps/Others/{sort_letter}/{package_id}/
```

Ledger: `dump_class = other`, `dump_system` / `dump_category` null.

## Classification and upload (implementation)

1. **`ingest-folder.py`** — after ClamAV, **`dump_classify.classify_dump_sip()`** on the SIP folder; ledger columns `dump_class`, `dump_system`, `dump_category`, `package_storage_prefix`, `storage_layout = bagit_unpacked`.
2. **`build-bagit-package.py`** — writes `wasabi_key` / staff URI into `metadata/submission.json` from the same classification.
3. **`upload-bag-wasabi.py`** — syncs the unpacked bag tree (all ingest kinds; bucket/prefix from ledger + `ingest-kinds.yaml`).

## Catalog enrichment

- **Redump:** SHA-1 from sidecar → redump.info → titles, contents, `dcterms:relation`; sidecar **System** / **Category** → `dcterms:format` / `dcterms:type`.
- **Moby:** optional synopsis and release date via **`game_catalog`** profile when `moby_game_id` or search resolves.

See [`hitsave-gdrive-intake/docs/redump-submissioninfo.md`](../../hitsave-gdrive-intake/docs/redump-submissioninfo.md) for sidecar → Omeka field mapping.

## Ledger schema

Migration **`schema/008_dump_storage.sql`**: `storage_layout`, `package_storage_prefix`, `dump_class`, `dump_system`, `dump_category`.

Migration **`schema/009_backfill_ingest_kind_dump.sql`**: `game_dump` / `disc_dump` → **`dump`**.

## Omeka (catalog-only)

- **`apply_dump_catalog_omeka.py`** — one CLI for metadata-only dump catalog items (unpacked BagIt on Wasabi, not Omeka file storage):
  - Ledger: `apply-dump-catalog-omeka.py <game.yaml> [handoff.yaml] [--item-id ID]` — Redump RDF from SIP + SHA-1 lookup, Moby from ledger, staff **`bibo:uri`** = ledger `aip_wasabi_prefix`.
  - Handoff YAML only: `apply-dump-catalog-omeka.py --manifest <handoff.yaml> [--item-id ID]` (via **`apply_handoff_dump_catalog()`**).
- **`register-dip-omeka-api.py`** delegates to **`apply_dump_catalog()`** when **`ingest_kind: dump`**.
- Redump sidecar parser SSOT: **`scripts/dump_redump.py`** (sync to gdrive via **`scripts/sync-redump-parser-to-gdrive.sh`**).

## GDrive intake

[**hitsave-gdrive-intake**](https://github.com/hitsave/hitsave-gdrive-intake) downloads from Drive, verifies Redump sidecar checksums, and stages **`ready/{game_key}/`** with `game.ingest.yaml` + `hitsave-submission.yaml`. Archiver **`ingest-folder.py`** performs BagIt and Wasabi upload (see [`gdrive-intake-handoff.md`](./gdrive-intake-handoff.md)).

## Related docs

- Press BagIt plan: [`oais-bagit-preservation-plan.md`](./oais-bagit-preservation-plan.md)
- Config contract: [`config-contract.md`](./config-contract.md)
