# Hit Save packaging standard

Hit Save preserves press material and dumps as **RFC 8493 BagIt** packages with payload under **`data/`**, fixity via **`manifest-sha256.txt`**, and sidecar metadata under **`metadata/`** (`submission.json`, `inventory.json`, optional Hit Save preview manifest).

Machine-readable settings: **`config/preservation/packaging.yaml`** (BagIt version, payload dir, Wasabi unpacked key pattern, validation).

Full OAIS mapping, pipeline, and Omeka access: **[oais-bagit-preservation-plan.md](./oais-bagit-preservation-plan.md)**.

## Validation

| Check | Tool |
|-------|------|
| Bag structure + manifests | `bagit-python` (`bag.validate()`) |
| Ingest gate | `build-bagit-package.py` after `make_bag` |
| Periodic fixity | `wasabi-fixity-check.py` on the Wasabi copy, every package at most every 90 days ([storage-retention.md](./storage-retention.md#fixity-schedule-wasabi-copy)); `fixity-check.py` for local bags |

## Wasabi

Unpacked bag trees only — one prefix per package. Press: `press-material/{sort_letter}/{package_id}/`. Dumps: see [dump-preservation.md](./dump-preservation.md).

## Omeka

OmekaDipViewer loads **`metadata/inventory.json`** from the Wasabi prefix and streams files under **`data/`** via S3. Registration is API-only (`register-dip-omeka-api.py`).
