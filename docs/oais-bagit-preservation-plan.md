# OAIS + BagIt preservation plan (Hit Save)

**Status:** Approved direction (replaces E-ARK as the primary packaging target for press material).  
**Audience:** Implementers, catalogers, and anyone asking “what standard do you use?”

---

## 1. Summary

Hit Save will preserve press material using **ISO 14721 (OAIS)** roles with **RFC 8493 BagIt** as the concrete package format:

| OAIS role | Hit Save implementation |
|-----------|-------------------------|
| **SIP** | Game folder (or approved submission archive) at ingest, with documented submission provenance |
| **AIP** | One **BagIt bag** per `game_key`, payload under **`data/`** |
| **DIP** | **Same bag, same `.tar`** — logical access package = physical archival package (no second full copy on Wasabi) |
| **Access** | **Omeka S** + **OmekaDipViewer** (updated to index BagIt `data/` instead of E-ARK METS) |

**Storage:** Each bag is serialized as a **plain `.tar`** (no additional gzip/zstd layer). One object per game on Wasabi; Omeka references or mounts that object — **not** a separate DIP tree plus unpacked AIP.

**E-ARK:** Legacy output remains on disk/Wasabi until re-ingested; **new and migrated press ingests** follow this plan. Do not invest in further E-ARK profile conformance.

---

## 2. Community press-material submission record

All material ingested from host path **`/tank2/press-material`** (container mount: **`/data/press-material`** when `HOST_PRESS_MATERIAL` points there) shares one **submission agreement / donation record**.

| Field | Value |
|-------|--------|
| **Submission type** | Community donation (press material) |
| **Submission date** | **2020-07-23** |
| **Scope** | Entire tree under `/tank2/press-material` unless a future config adds another source |
| **Internal source id** | `hitsave-submission:press-material-community-2020-07-23` |
| **SIP definition** | One **game directory** (immediate child folder or configured `source_subpath`) = one SIP instance; shared provenance above applies to every such SIP |

This record MUST appear in:

1. **Config** — `config/preservation/submission-sources.yaml` (see §8).
2. **Every BagIt `bag-info.txt`** for ingests from this source (via `External-Identifier` / `External-Description`).
3. **Ledger** — `game_ingest` (or successor) columns / JSON for `submission_source_id`, `submission_date`, `sip_path`.
4. **Omeka** — collection- or item-level provenance (e.g. `dcterms:provenance`) applied at upload from config so catalogers do not re-type it.

Optional later: attach donor agreement PDF under `data/metadata/` or store a hash + URL in `bag-info.txt`; not required for phase 1.

---

## 3. OAIS function mapping

```text
/tank2/press-material/{game}/     ──►  Ingest (SIP)
        │                              ClamAV, policy, hash inventory
        ▼
   BagIt bag (data/…)               ──►  AIP (canonical)
        │
        ▼
   {game_key}.tar on Wasabi         ──►  AIP + DIP (single stored object)
        │
        ▼
   Omeka item + DipViewer           ──►  Access (metadata + browse tar / mount)
```

| Function | Component |
|----------|-----------|
| Ingest | `ingest-worker`, `ingest-game-folder.py`, submission scripts |
| Archival storage | Wasabi `hitsave-incoming` (or successor bucket/prefix), one `.tar` per game |
| Data management | Postgres ledger, optional `inventory.json` inside bag |
| Access | Omeka REST uploader, OmekaDipViewer (BagIt-aware) |
| Administration | `status-web`, config repo, ingest logs |
| Preservation planning | Light policy in this doc + annual review (format counts from inventory) |

---

## 4. BagIt layout (`data/` payload)

Each game produces **one bag directory** before tarring. Payload lives only under **`data/`** (US-familiar, Archivematica-adjacent, easy to explain).

```text
{game_key}/                          ← bag root (becomes root of .tar)
├── bagit.txt                        ← BagIt-Version, Tag-File-Character-Encoding
├── bag-info.txt                     ← submission + Hit Save fields (§5)
├── manifest-sha256.txt              ← fixity over payload + tag files per RFC 8493
├── tagmanifest-sha256.txt           ← fixity over tag files (recommended)
├── metadata/                        ← not part of payload; tag files or metadata-in-tagmanifest
│   ├── submission.json              ← machine-readable submission record (§5)
│   └── inventory.json               ← per-file path, size, sha256, mime, optional pronom
└── data/                            ← **press material payload** (mirrors source tree)
    ├── screenshot.png
    ├── trailer.mp4
    └── …
```

**Rules:**

- All press files under `data/`; preserve relative paths from the SIP folder.
- `manifest-sha256.txt` lists every payload file under `data/` and required tag files per RFC 8493.
- Validate with **`bagit-python`** before upload (`--validate`).
- **No METS.xml** at bag root for phase 1 (DipViewer switches to BagIt + inventory; see §7).

**Hash algorithm:** SHA-256 (manifest name `manifest-sha256.txt`) for broad tool support.

---

## 5. `bag-info.txt` and `metadata/submission.json`

### 5.1 `bag-info.txt` (human + agent readable)

Example keys for press-material donation ingests:

```text
Bagging-Date: 2026-01-15
Bag-Software: HitSave ingest-worker
Bag-Software-Version: <from config>
External-Identifier: hitsave-submission:press-material-community-2020-07-23
External-Description: Community press-material donation received 2020-07-23; game_key=<game_key>
Organization-Address: Hit Save
Internal-Sender-Identifier: /tank2/press-material/<relative-path>
```

### 5.2 `metadata/submission.json` (canonical machine record)

```json
{
  "submission_source_id": "hitsave-submission:press-material-community-2020-07-23",
  "submission_type": "community_donation",
  "submission_date": "2020-07-23",
  "sip_path": "/data/press-material/...",
  "game_key": "example-game",
  "ingested_at": "ISO-8601",
  "repository_code": "hitsave"
}
```

`inventory.json`: one row per file under `data/` for archive-wide queries (e.g. count `.mp4` across bags via batch job over Wasabi or ledger exports).

---

## 6. Single `.tar` as AIP and DIP (storage)

### 6.1 Intent

- **One** Wasabi object per game: **`{prefix}{game_key}/{game_key}.tar`** (exact key pattern in config).
- Tar contains the **bag root** (`bagit.txt` at tar root, `data/` inside).
- **No** separate `aip/` unpacked tree and `dip/` second tar with duplicate bytes.
- **No** extra compression beyond tar (tar is an aggregation format only; files inside remain stored as-is).

### 6.2 Omeka

- Omeka item = catalog + search metadata (Moby, titles, donation provenance).
- **One media** (or `external_tar_path`) points at the **same** `.tar` on Wasabi when using dip-wasabi mount, **or** media is uploaded once from that tar — still **one** Wasabi copy if upload is avoided via mount.
- Prefer **mount + external path** (existing dip-wasabi pattern) to avoid duplicating the tar in Omeka’s volume.

### 6.3 Policy statement (public)

> For community press material, Hit Save’s archival package is a BagIt bag. The same package is used for long-term storage and for access in Omeka. We store one tar archive per game on Wasabi.

---

## 7. OmekaDipViewer changes (dependency)

**Repository:** [omeka-dip-viewer](https://github.com/hitsave/omeka-dip-viewer) (or current fork).

**Today:** `MetsParser` expects E-ARK DIP — root `METS.xml`, `representations/.../data/`.

**Target:**

1. Detect package type: **BagIt** if `bagit.txt` present at tar root (after extract or streaming index).
2. Index files under **`data/`** for tree UI (paths relative to `data/`).
3. Optional: read **`metadata/inventory.json`** for mime/type badges without re-probing.
4. Keep E-ARK path as **legacy** for old tars until migration completes.

**Acceptance test:** Browse `E2E/TestGame` press bag tar in Omeka test stack; files listed match `data/` layout.

---

## 8. Configuration (files, not environment)

| File | Purpose |
|------|---------|
| `config/preservation/submission-sources.yaml` | Donation record, path prefix → source id mapping |
| `config/preservation/packaging.yaml` | Update: `packaging_mode: bagit`, tar layout, manifest algorithm |
| `config/preservation/ingest.yaml` | Wasabi prefix, single-object key pattern; deprecate separate `aip_subdir` unpacked layout for press |
| `config/omeka-uploader.yaml` | Point DipViewer at bag tar; provenance fields from submission source |

### 8.1 `submission-sources.yaml` (to add)

```yaml
sources:
  press_material_community_donation:
    id: hitsave-submission:press-material-community-2020-07-23
    type: community_donation
    submission_date: "2020-07-23"
    host_path_prefix: /tank2/press-material
    container_path_prefix: /data/press-material
    apply_to_ingest: true
    bag_info_external_identifier: hitsave-submission:press-material-community-2020-07-23
    omeka_provenance_text: >
      Press material from a community donation received on 2020-07-23.
```

Ingest resolves source by longest matching `container_path_prefix` on `source_game_folder`.

---

## 8.2 Operational DB vs published preservation catalog (Git)

**Agreed model:**

| Layer | Technology | Lifetime |
|-------|------------|----------|
| **Operations** | **Postgres** `game_ingest` | Live during ingest, upload, Moby, fixity, status-web |
| **Preservation catalog** | **`preservation-catalog.json`** (committed snapshot) | **Stable git commit** after batch or release — not a live DB |
| **Future** | Import catalog JSON into an online DB | Out of scope until needed |

Postgres stays. The GitHub artifact is a **deliberate export**, regenerated and committed when you choose (e.g. after a batch finishes), not continuously synced.

### BagIt inventory inside the catalog — yes, it works

Each bag already carries **`metadata/inventory.json`** (see §4) — path, size, SHA-256, mime (optional extension counts). The published catalog **embeds that inventory per game** so one file supports:

- GitHub browse/diff history  
- “How many `.mp4` files?” → sum `games[].inventory.summary.by_extension.mp4` or scan `files[]`  
- Later **bulk import** into SQLite/Postgres/ClickHouse without re-reading every tar on Wasabi  

**Source of truth order:**

1. **Inside the archived bag** (`metadata/inventory.json`) — travels with the tar forever.  
2. **Inside the committed catalog** — copy embedded at export time (denormalized on purpose).  
3. **Postgres** — optional `inventory_json` JSONB snapshot at ingest `complete` so export does not re-download every tar (recommended).

Ingest never commits to git; it writes the bag + ops row. **`scripts/export-preservation-catalog.py`** builds the catalog from Postgres (complete rows + stored inventory snapshot) or, if missing, reads `metadata/inventory.json` from the local bag dir / one-time tar extract.

### `preservation-catalog.json` shape (v1)

Single JSON document in private git, e.g. `preservation/preservation-catalog.json`:

```json
{
  "catalog_version": 1,
  "exported_at": "2026-04-01T12:00:00Z",
  "repository_code": "hitsave",
  "submission_sources": {
    "hitsave-submission:press-material-community-2020-07-23": {
      "submission_date": "2020-07-23",
      "type": "community_donation"
    }
  },
  "games": [
    {
      "game_key": "example-game",
      "submission_source_id": "hitsave-submission:press-material-community-2020-07-23",
      "sip_path": "/data/press-material/Publisher/example-game",
      "ingested_at": "2026-03-15T…",
      "package": {
        "format": "bagit",
        "tar_sha256": "…",
        "wasabi_bucket": "hitsave-incoming",
        "wasabi_key": "press-material/example-game/example-game.tar"
      },
      "omeka": { "item_id": 123, "media_id": 456 },
      "moby_title": "…",
      "inventory": {
        "manifest_version": 1,
        "files": [
          {
            "path": "data/trailer.mp4",
            "size_bytes": 1048576,
            "sha256": "…",
            "mime": "video/mp4"
          }
        ],
        "summary": {
          "file_count": 42,
          "total_bytes": 999,
          "by_extension": { "mp4": 3, "png": 10, "pdf": 1 }
        }
      }
    }
  ]
}
```

Optional derived **`preservation-catalog-summary.csv`** (one row per game, no `files[]`) for spreadsheets — generated from the same script, same commit.

### Workflow

```text
Ingest → BagIt (inventory.json in bag) → tar → Wasabi
              ↓
         Postgres (status, ids, optional inventory_json snapshot)
              ↓
    export-preservation-catalog.py   ← manual or after batch script
              ↓
    git commit preservation-catalog.json   ← stable ledger you meant
```

### Repo and privacy

Commit from **`hitsave-archiver-config`** (private) or a dedicated **`hitsave-preservation-catalog`** repo. Public archiver repo should not hold full catalogs unless redacted.

### Analytics examples

- **MP4 count (archive-wide):** `sum(g.inventory.summary.by_extension.mp4 for g in games)`  
- **Games with any video:** filter where `by_extension.mp4` or `mime` prefix `video/` in files  
- **Future online DB:** import `games[]` + unnest `inventory.files[]` into a `files` table keyed by `game_key`

---

## 9. Ingest pipeline (target behavior)

Replace dual **`build-aip-e-ark.py`** + **`build-dip-e-ark.py`** for press material with:

1. **`build-bagit-package.py`** (new) — scan SIP → populate `data/` → write tags, manifests, `submission.json`, `inventory.json` → validate BagIt.
2. **`tar-bag.sh` or Python tar step** — ` {output}/{game_key}.tar` from bag directory.
3. **`upload-package-wasabi.py`** — put single tar; ledger stores `package_tar_key`, tar SHA-256, `submission_source_id`.
4. **`upload-dip-omeka-api.py`** — unchanged flow except media = bag tar; provenance from submission source config.
5. **Remove from hot path:** E-ARK validate, Siegfried/PREMIS3 unless later added as optional inventory enrichment.

**Postgres (`game_ingest`):** track `package_format: bagit`, `package_tar_sha256`, `submission_source_id`, optional **`inventory_json`** (copy of bag `metadata/inventory.json` at `complete`); retain `game_key`, Omeka ids, status enum. **Git catalog:** separate export (§8.2).

---

## 10. Validation and fixity

| Check | When |
|-------|------|
| `bagit-python --validate` | After bag build, before tar |
| SHA-256 of `.tar` | After tar; stored in ledger |
| Periodic re-hash of Wasabi object | `fixity-check.py` adapted for tar keys |
| ClamAV | Unchanged, on SIP before bagging |

---

## 11. Migration from E-ARK

| Phase | Action |
|-------|--------|
| **0** | Land this plan, `submission-sources.yaml`, and config stubs; no user-facing change |
| **1** | Implement `build-bagit-package.py` + tar + Wasabi upload; CI game `E2E/TestGame` |
| **2** | OmekaDipViewer BagIt mode; hitsave-omeka-test verification |
| **3** | New ingests from `/tank2/press-material` use BagIt-only; ledger flags format |
| **4** | Optional batch re-ingest: E-ARK games → BagIt tar, delete redundant unpacked AIP + duplicate DIP tar on Wasabi |
| **5** | Mark E-ARK scripts deprecated; trim `preservation-packaging-standard.md` to point here |

**Legacy:** Existing E-ARK tars remain browsable until viewer BagIt ship + optional re-ingest.

---

## 12. Documentation and OAIS wording

**Short external statement:**

> Hit Save’s preservation system follows ISO 14721 (OAIS): we ingest submission packages (community press folders), store **RFC 8493 BagIt** archives on Wasabi, and provide access through Omeka. For our press collection, the archival package and the access package are the same BagIt tar.

Avoid claiming “E-ARK compliant.” Optional: “BagIt-validated” with tool name and date.

**Internal:** Link Omeka cataloging time to §2 provenance (auto-applied), not manual entry per game.

---

## 13. Open decisions (defaults chosen)

| Topic | Decision |
|-------|----------|
| Compression | **Tar only**, no gzip/zstd wrapper |
| Payload directory | **`data/`** required |
| AIP vs DIP | **Same object** on Wasabi |
| Donation date | **2020-07-23** for all `/tank2/press-material` |
| Format ID | **inventory.json** with extension + mime at ingest; PRONOM optional later |
| Portable zip submissions | Separate submission source entry in YAML when defined (not the 2020 donation) |

---

## 14. Implementation checklist

- [ ] Add `config/preservation/submission-sources.yaml` with donation record
- [ ] Add `docs/oais-bagit-preservation-plan.md` (this file) to README / AGENTS pointers
- [ ] Implement `build-bagit-package.py` + tests (E2E fixture)
- [ ] Wire `ingest-game-folder.py` to BagIt + tar path for press source
- [ ] Wasabi uploader: single tar key; drop unpacked AIP upload for press
- [ ] Ledger schema migration for `submission_source_id`, `package_format`, tar digest
- [ ] `export-preservation-catalog.py` → `preservation-catalog.json` (+ optional summary CSV) with embedded BagIt inventory; commit to private git
- [ ] Postgres `inventory_json` snapshot at ingest complete (feeds export without re-reading tar)
- [ ] Omeka uploader: provenance from submission source config
- [ ] OmekaDipViewer: BagIt `data/` indexer + legacy E-ARK
- [ ] Update `fixity-check.py` for tar objects
- [ ] Deprecate E-ARK builders for press in docs; archive `preservation-packaging-standard.md` E-ARK sections

---

## 15. References

- [ISO 14721 (OAIS)](https://www.iso.org/standard/7898.html) — reference model
- [RFC 8493 (BagIt)](https://www.datatracker.ietf.org/doc/html/rfc8493)
- [bagit-python](https://github.com/LibraryOfCongress/bagit-python)
- Hit Save: `docs/config-contract.md`, `config/preservation/ingest.yaml`, `docs/dip-wasabi-external-mount.md`
