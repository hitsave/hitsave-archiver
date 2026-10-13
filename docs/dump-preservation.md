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
| `dump` | unpacked prefix (below) | yes (`game_catalog`) | when sidecar + SHA-1 |
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
dumps/{System}/{Category}/{package_id}/
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

- `dumps/DVD-Video/Coverdiscs/{package_id}/` — coverdisc such as [disc 91919](https://redump.info/disc/91919)
- `dumps/PlayStation/Games/{package_id}/` — e.g. [disc 134898](https://redump.info/disc/134898)

Path segments use Redump **System** and **Category** strings with `/` and `\` replaced by `-`. **`package_id`** is the same `{title_slug}-{uuid_v7}` identity as press ingest.

Staff URI (Omeka / ledger): `s3://hitsave-incoming/dumps/PlayStation/Games/{package_id}/`

### Other dumps

SIPs without a valid Redump sidecar are stored until dedicated automation exists:

```text
dumps/Others/{package_id}/
```

Ledger: `dump_class = other`, `dump_system` / `dump_category` null.

## Classification and upload (implementation)

1. **`ingest-game-folder.py`** — after ClamAV, **`dump_classify.classify_dump_sip()`** on the SIP folder; ledger columns `dump_class`, `dump_system`, `dump_category`, `package_storage_prefix`, `storage_layout = bagit_unpacked`.
2. **`build-bagit-package.py`** — writes `wasabi_key` / staff URI into `metadata/submission.json` from the same classification.
3. **`upload-dump-wasabi.py`** — syncs the unpacked bag tree to the computed prefix.

Press ingest uses the same unpacked layout via **`upload-press-bag-wasabi.py`**.

## Catalog enrichment

- **Redump:** SHA-1 from sidecar → redump.info → titles, contents, `dcterms:relation`; sidecar **System** / **Category** → `dcterms:format` / `dcterms:type`.
- **Moby:** optional synopsis and release date via **`game_catalog`** profile when `moby_game_id` or search resolves.

See [`hitsave-gdrive-intake/docs/redump-submissioninfo.md`](../../hitsave-gdrive-intake/docs/redump-submissioninfo.md) for sidecar → Omeka field mapping.

## Ledger schema

Migration **`schema/008_dump_storage.sql`**: `storage_layout`, `package_storage_prefix`, `dump_class`, `dump_system`, `dump_category`.

Migration **`schema/009_backfill_ingest_kind_dump.sql`**: `game_dump` / `disc_dump` → **`dump`**.

## Omeka (catalog-only)

- **`apply_dump_catalog_omeka.py`** — Redump RDF from SIP sidecar + SHA-1 lookup, Moby from ledger, staff **`bibo:uri`** = ledger `aip_wasabi_prefix` (`s3://…/dumps/…/`). No DIP tar media.
- **`register-dip-omeka-api.py`** delegates to the same path when **`ingest_kind: dump`**.
- GDrive intake handoff YAML can still use **`apply-redump-catalog-omeka.py`**; it shares **`dump_catalog_omeka.build_redump_values`**.

## GDrive intake

Drive poller uploads raw dump folders to the **same Wasabi prefix** (`intake/dump_layout.py`) before archiver BagIt ingest.

## Related docs

- Press BagIt tar plan: [`oais-bagit-preservation-plan.md`](./oais-bagit-preservation-plan.md)
- Config contract: [`config-contract.md`](./config-contract.md)
