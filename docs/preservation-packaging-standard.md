# Hit Save preservation packaging standard (tentative)

**Decision (testing phase):** **Preservation = [E-ARK AIP](https://aip.openpreservation.org/) ([CSIP](https://earkcsip.dilcis.eu/)); access = [E-ARK DIP](https://dip.openpreservation.org/).**

Machine-readable pins: `config/preservation/packaging.yaml`.

This document is the normative overview. Ingest builders are **`build-aip-e-ark.py`** and **`build-dip-e-ark.py`**; validate with **`scripts/validate-e-ark-package.sh`** before upload.

---

## 1. Why this stack

| Goal | How E-ARK helps |
|------|------------------|
| **Documented, testable packages** | CSIP + AIP/DIP specs + [eark-validator](https://github.com/E-ARK-Software/eark-validator) and [Commons IP](https://keeps.github.io/commons-ip/) |
| **OAIS roles** | Clear **AIP** (preservation) vs **DIP** (access) |
| **US interoperability (conceptual)** | Same **LoC [METS](https://www.loc.gov/standards/mets/)** and **[PREMIS](https://www.loc.gov/standards/premis/)** that **Archivematica** uses; US peers often say “BagIt / AM AIP” — see §5 |
| **EU interoperability** | eArchiving validators and [test corpus](https://github.com/DILCISBoard/eark-ip-test-corpus) |

We are **not** running Archivematica. We **are** aligning package *shape and metadata* where it helps US and EU audiences map our outputs to tools they already know.

---

## 2. Standards map (read this first)

```text
OAIS (ISO 14721)          — concepts: SIP, AIP, DIP
        │
        ▼
E-ARK CSIP 2.x            — common folder + METS + PREMIS rules (all IP types)
        │
        ├── E-ARK AIP       — preservation master → Wasabi (unpacked package root)
        │
        └── E-ARK DIP       — access package → Omeka (.tar acceptable as transport)
        │
LoC METS 1.x               — structural / administrative metadata (inside package)
LoC PREMIS 3.x             — preservation metadata (typically METS amdSec)
        │
RFC 8493 BagIt (optional) — export/interchange wrapper only (§6)
```

**Read order for implementers:** [CSIP](https://earkcsip.dilcis.eu/) → [AIP spec](https://aip.openpreservation.org/) or [DIP spec](https://dip.openpreservation.org/) → [CS PREMIS](https://citspremis.dilcis.eu/specification/).

---

## 3. E-ARK AIP (preservation master)

### 3.1 Role

- Long-term custody on **Wasabi** (unpacked files under `config/preservation/ingest.yaml` → `wasabi.prefix` + `game_key`).
- Source of truth for **fixity**, audit, and future DIP regeneration.
- **One AIP per game folder** (same granularity as Omeka item).

### 3.2 Target layout (CSIP + E-ARK AIP)

At minimum (CSIP package root — **not** BagIt `data/data/` mirror):

```text
{game_key}/                    ← package root on Wasabi
├── METS.xml                   ← AIP METS (profile + OAISPACKAGETYPE=AIP per specs)
├── metadata/                  ← descriptive / additional metadata as required by CSIP
├── representations/           ← CSIP representation structure (see CSIP §4)
│   └── …                      ← payload + PREMIS in METS amdSec
├── documentation/             ← optional per CSIP
└── …                          ← schemas/ if bundled for validation (optional)
```

Exact folder names and METS `@PROFILE` values **must** match the E-ARK AIP specification for `csip_version` in `packaging.yaml` (currently **2.2.0**). Do not invent a parallel tree; implement from the spec appendices and validate.

### 3.3 What we store in the ledger

Postgres `game_ingest`: `aip_wasabi_prefix`, checksum pointers, `package_uuid` (METS/OAIS identifiers), fixity timestamps — see `schema/`.

---

## 4. E-ARK DIP (access)

### 4.1 Role

- **One DIP per game**, one Omeka media row (`omeka_dip_package`), browsed via **OmekaDipViewer**.
- May include **access-friendlier** copies (e.g. MP4 access derivatives) where policy allows; originals remain in the AIP.

### 4.2 Target layout

- **Logical package:** E-ARK DIP (CSIP-compliant METS with `OAISPACKAGETYPE="DIP"` and DIP METS `@PROFILE` from the DIP spec).
- **Physical transfer to Omeka:** **`.tar`** containing the DIP root (current worker pattern) is fine; tar is **transport**, not the standard itself.

Builders **`scripts/build-dip-e-ark.py`** and **`scripts/build-aip-e-ark.py`** emit CSIP layout (`METS.xml`, `representations/rep1/data/…`, optional `access/` copies) and must pass **`scripts/validate-e-ark-package.sh`** before upload.

### 4.3 OmekaDipViewer

The viewer indexes **root `METS.xml`** and payload under **CSIP `representations/…/data/`** (plus `USE="access"` members when present). Legacy Archivematica `objects/` + `METS.{uuid}.xml` layouts are not supported.

---

## 5. US alignment (Archivematica / BagIt — clarification only)

This section is for **communication with US archives**, not a second normative standard.

| US practice | Hit Save mapping |
|-------------|------------------|
| **Archivematica AIP** | BagIt envelope with `data/METS.{uuid}.xml`, `data/objects/`, logs | **E-ARK AIP** uses **CSIP METS at package root** and representation folders — **conceptually** the same METS+PREMIS story, **different** folder profile. Validators differ. |
| **Archivematica DIP** | METS + access files for AtoM/Archivematica access | **E-ARK DIP** — same METS/PREMIS family; profile URLs and CSIP headers differ. |
| **RFC 8493 BagIt** | APTrust, LoC transfer, many US repos | **Optional export** (§6), not the primary Wasabi layout. |
| **NDSA Levels** | Program assessment | Use for **operations maturity**, not package serialization. |

**One-line for partners:** *“Hit Save packages are E-ARK CSIP AIPs and DIPs using Library of Congress METS and PREMIS; they are conceptually comparable to Archivematica information packages.”*

---

## 6. Optional BagIt (RFC 8493) export

For partners or tools that only accept BagIt:

1. Build a valid **E-ARK AIP** directory.
2. Optionally wrap it: BagIt `data/` contains the **entire AIP root** or the representation payload per agreed profile.
3. Validate **both** BagIt (`bagit-python`) and **E-ARK** (`eark-validator`) when offering BagIt interchange.

Primary preservation storage remains **unpacked E-ARK AIP** on Wasabi unless policy changes.

---

## 7. Ingest boundary (SIP)

- **Today:** ingest reads a **game folder** on tank (`/data/press-material/...`) — internal staging, not a formal **E-ARK SIP**.
- **Later:** if a producer delivers **E-ARK SIP**, add ingest validation and map SIP → AIP per CSIP.
- Config: `packaging.yaml` → `sip_profile: deferred`.

---

## 8. Validation and testing (required in CI / pilot gate)

Every new builder version should pass validation on at least one pilot game before merge/deploy.

### 8.1 Primary: eark-validator (Python)

```bash
# Host or venv (Python 3.10+)
pip install eark-validator
eark-validator /path/to/unpacked-aip-or-dip-root/
```

Repo wrapper (runs validator in Docker if not installed locally):

```bash
bash scripts/validate-e-ark-package.sh /path/to/package-root
```

### 8.2 Alternate: Commons IP (Java)

```bash
java -jar commons-ip-cli-*.jar validate -i /path/to/package.zip -o /tmp/reports -v
```

Supports CSIP 2.x and E-ARK SIP/AIP/DIP; useful for ZIP-serialized packages and detailed reports.

### 8.3 Online (manual spot checks)

[eArchiving Validation Service](https://seal.e-ark-foundation.eu/earchiving-validation-service/) — upload package for Commons IP / PyIP validators.

### 8.4 Regression corpus

Clone [DILCISBoard/eark-ip-test-corpus](https://github.com/DILCISBoard/eark-ip-test-corpus) and run `eark-validator` on CSIP/AIP/DIP **valid** examples when upgrading `csip_version` or validator versions.

### 8.5 Hit Save pilot checklist

| Step | AIP | DIP |
|------|-----|-----|
| Build from pilot game config | `build-aip-e-ark.py` via `ingest-game-folder.py` | `build-dip-e-ark.py` |
| Validate structure | `scripts/validate-e-ark-package.sh` | same (DIP root extracted from tar) |
| Upload | Wasabi per `docs/preservation-wasabi.md` | `upload-dip-omeka-api.py` |
| Access smoke test | — | OmekaDipViewer browse + fixity sample |

### 8.6 What we do not use as conformance gates

- NDSA Levels (program rubric, not package schema).
- Omeka admin UI alone (access test, not preservation schema).

---

## 9. Implementation status

| Component | Standard target | Current code |
|-----------|-----------------|--------------|
| Wasabi upload | Unpacked package root | `upload-aip-wasabi.py` (E-ARK root: `METS.xml`, `representations/rep1/data/…`) |
| AIP builder | E-ARK AIP + CSIP | **`build-aip-e-ark.py`** via `ingest-game-folder.py`; pilot **`pilot-wog1`** passes **`validate-e-ark-package.sh`** |
| DIP builder | E-ARK DIP | **`build-dip-e-ark.py`** (root `METS.xml` + `representations/rep1/data`, `.tar` for Omeka) |
| Validation script | `validate-e-ark-package.sh` | **`eark-validator`** schema + schematron (ERROR-level) |
| Omeka viewer | E-ARK DIP or documented profile | **`MetsParser`** indexes E-ARK `Data` + `access` fileGrps (legacy `objects/` still supported) |

**Note:** For `csip:CONTENTINFORMATIONTYPE="MIXED"` at the package root, each `fileSec/fileGrp` uses `OTHER` + `csip:OTHERCONTENTINFORMATIONTYPE` so `eark-validator` CSIP63 passes under XPath 2.0 (RODA sample AIPs omit fileGrp CIT and fail the same rule).

---

## 10. References

- [E-ARK CSIP](https://earkcsip.dilcis.eu/)
- [E-ARK AIP](https://aip.openpreservation.org/)
- [E-ARK DIP](https://dip.openpreservation.org/)
- [Archivematica AIP structure](https://www.archivematica.org/en/docs/archivematica-1.18/user-manual/archival-storage/aip-structure/) (US comparison)
- [RFC 8493 BagIt](https://www.datatracker.ietf.org/doc/html/rfc8493) (optional interchange)
- [plan.md §15](../plan.md) — pipeline context
