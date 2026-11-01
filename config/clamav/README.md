# ClamAV for preservation ingest

`clamd.conf` is bind-mounted into the `clamav` service. It is intentionally **small**: the image ships a full template, but we only commit the directives we override.

| Setting | Why |
|---------|-----|
| `TCPSocket 3310` | `ingest-worker` uses `pyclamd` over the Compose network (`clamav:3310`). |
| `MaxFiles` / `MaxRecursion` | Align with `config/preservation/submissions.yaml` → `limits.max_archive_members` (50000) and archive depth. |
| `MaxFileSize`, `MaxScanSize`, `StreamMaxLength`, `PCREMaxFileSize` = `0` | No limit below libclamav's own ~2 GiB per-object cap. Larger files are scanned by [`scripts/clamav_full_scan.py`](../../scripts/clamav_full_scan.py): disc images file by file (plus the bytes between files), anything else in overlapping raw chunks. Every byte is scanned. |
| `AlertExceedsMax yes` | A hit limit is reported as `Heuristics.Limits.Exceeded.*` instead of passing silently; ingest records that file as not fully scanned (PREMIS `partial`). |
| `MaxScanTime 3600000` | One hour per object instead of the 2-minute default (which also passed silently). |

SSOT map (which file owns limits vs daemon caps): [docs/config-contract.md](../../docs/config-contract.md). Portable submission security: [docs/portable-submissions.md](../../docs/portable-submissions.md).

After editing this file:

```bash
docker compose up -d --force-recreate clamav
```

Wait until the container is **healthy** before running ingest.
