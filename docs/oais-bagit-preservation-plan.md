# OAIS + BagIt preservation (Hit Save)

How Hit Save packages community press material and dumps: **ISO 14721 (OAIS)** roles implemented with **RFC 8493 BagIt**, stored on **Wasabi**, and browsed in **Omeka S** via **OmekaDipViewer** and the S3 API.

Normative machine config: `config/preservation/packaging.yaml`, `config/preservation/ingest.yaml`, `config/preservation/submission-sources.yaml`.

---

## 1. Summary

| OAIS role | Implementation |
|-----------|----------------|
| **SIP** | Game folder under press material, or an approved submission archive |
| **AIP / DIP** | One BagIt bag per package; payload under `data/` |
| **Storage** | Unpacked bag on Wasabi (`press-material/{sort_letter}/{package_id}/` for press; see [dump-preservation.md](./dump-preservation.md) for dumps) |
| **Access** | Omeka item + `omeka_dip_package` media; DipViewer reads `metadata/inventory.json` over S3 |
| **Fixity** | `manifest-sha256.txt` in the bag; `fixity-check.py` runs `bagit.validate()` and spot-checks against `metadata/inventory.json` |

---

## 2. Press-material submission record

Material under **`/data/press-material`** (host path configured via `HOST_PRESS_MATERIAL`) shares one donation record in `config/preservation/submission-sources.yaml`.

| Field | Example |
|-------|---------|
| Submission type | Community donation (press material) |
| Submission date | 2020-07-23 |
| Internal source id | `hitsave-submission:press-material-community-2020-07-23` |
| SIP | One game directory = one ingest package |

Provenance is copied into each bag (`bag-info.txt`, `metadata/submission.json`) and the Postgres ledger (`submission_source_id`, `sip_fingerprint`, `package_id`).

---

## 3. Pipeline

```text
/data/press-material/{game}/     →  ClamAV + policy
        │
        ▼
   build-bagit-package.py        →  local bag under /output/bag/…
        │
        ▼
   upload-press-bag-wasabi.py    →  Wasabi prefix press-material/{letter}/{package_id}/
        │
        ▼
   register-dip-omeka-api.py       →  Omeka item + S3-backed DIP media
```

| Step | Script / component |
|------|---------------------|
| Ingest | `ingest-game-folder.py` (ingest-worker) |
| Bag build | `build-bagit-package.py` |
| Wasabi | `upload-press-bag-wasabi.py` (press), `upload-dump-wasabi.py` (dumps) |
| Omeka | `register-dip-omeka-api.py` |
| Fixity | `fixity-check.py` |
| Catalog export | `export-preservation-catalog.py` |

---

## 4. BagIt layout

```text
{bag_root}/
├── bagit.txt
├── bag-info.txt
├── manifest-sha256.txt
├── tagmanifest-sha256.txt          (when tag files are manifested)
├── metadata/
│   ├── submission.json
│   ├── inventory.json
│   └── hitsave/preview-manifest.json   (grid/browse thumbs for Omeka)
└── data/                           ← press files (relative paths preserved)
```

- Validate after build: `bagit-python` (`bag.validate()` in `build-bagit-package.py`).
- **`metadata/inventory.json`**: per-file `path`, `size_bytes`, `sha256`, `mime`; Omeka indexes browseable files from this file on S3.
- **`metadata/submission.json`**: `package_id`, `package_storage_prefix`, `wasabi_key`, submission ids, ingest timestamps (see builder output).

---

## 5. Wasabi keys

Press material (from `packaging.yaml` / `ingest.yaml`):

```text
s3://hitsave-incoming/press-material/{sort_letter}/{package_id}/bagit.txt
```

Dumps use prefixes from [dump-preservation.md](./dump-preservation.md) (`ingest-kinds.yaml`).

Ledger column **`package_storage_prefix`** is the Omeka/DipViewer prefix (no trailing slash required in API; URIs use `s3://bucket/prefix/`).

---

## 6. Omeka access

One **`omeka_dip_package`** media row per package. Media `data` includes:

- `dip_storage`: `wasabi-s3`
- `package_storage_prefix`: e.g. `press-material/F/my-game-01947c2a-…`
- `dip_wasabi_uri` / `package_wasabi_uri`: `s3://hitsave-incoming/…/`

No file upload into Omeka storage. Test stack: render `dip-s3.config.php` and use `docker-compose.s3-direct.yml` ([hitsave-omeka-test docs](https://github.com/hitsave/hitsave-omeka-test/blob/main/docs/dip-wasabi-s3-direct.md)).

Staff preservation URI: `omeka_staff_metadata.py` → `dcterms:relation` from ledger prefix.

---

## 7. Dumps

Ingest kind **`dump`**: unpacked BagIt under `dumps/{System}/{Category}/{package_id}/` (or `dumps/Others/…`). Omeka catalog is metadata-only via `apply_dump_catalog_omeka.py`. Details: [dump-preservation.md](./dump-preservation.md).

---

## 8. Published catalog (Git)

Operations live in **Postgres** (`game_ingest`). A **preservation catalog** JSON export (`export-preservation-catalog.py`) can be committed to a private repo for history and analytics; each game row may embed a copy of `inventory_json` from the ledger.

Example package fields:

```json
"package": {
  "format": "bagit",
  "wasabi_bucket": "hitsave-incoming",
  "package_storage_prefix": "press-material/T/tekken-7-01947c2a-8b3d-7a1e-b2c4-9d8e1f2a3b4c"
}
```

---

## 9. External wording

> Hit Save follows ISO 14721 (OAIS): we ingest submission packages, store RFC 8493 BagIt archives on Wasabi, and provide access through Omeka. For press material, the archival package and the access package are the same BagIt bag.

Optional: cite BagIt validation tool and date from `bag-info.txt` (`Bag-Software` / `Bag-Software-Version`).

---

## 10. References

- [RFC 8493 (BagIt)](https://www.datatracker.ietf.org/doc/html/rfc8493)
- [bagit-python](https://github.com/LibraryOfCongress/bagit-python)
- [dip-wasabi-s3.md](./dip-wasabi-s3.md) — Omeka + Wasabi
- [config-contract.md](./config-contract.md)
