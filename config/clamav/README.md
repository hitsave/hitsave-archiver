# ClamAV for preservation ingest

`clamd.conf` is bind-mounted into the `clamav` service. It is intentionally **small**: the image ships a full template, but we only commit the directives we override.

| Setting | Why |
|---------|-----|
| `TCPSocket 3310` | `ingest-worker` uses `pyclamd` over the Compose network (`clamav:3310`). |
| `MaxFiles` / `MaxRecursion` | Align with `config/preservation/submissions.yaml` → `limits.max_archive_members` (50000) and archive depth. |
| `MaxFileSize`, `MaxScanSize`, `StreamMaxLength`, `PCREMaxFileSize` = `0` | No limit below libclamav's own ~2 GiB per-object cap. Larger files are scanned by [`scripts/clamav_full_scan.py`](../../scripts/clamav_full_scan.py): disc images file by file (plus the bytes between files), anything else in overlapping raw chunks. Every byte is scanned. |
| `AlertExceedsMax yes` | A hit limit is reported as `Heuristics.Limits.Exceeded.*` instead of passing silently; ingest records that file as not fully scanned (PREMIS `partial`). |
| `AlertEncryptedArchive yes` | A password-protected archive whose contents clamd cannot open is reported (`Heuristics.Encrypted.*`) and recorded as not fully scanned, instead of passing as clean. Encrypted documents are not flagged (owner-password PDFs are still fully readable). |
| `MaxScanTime 0` | No scan time limit (the 2-minute default also passed unfinished scans as clean). |
| `MaxFiles 0`, `MaxRecursion 100`, byte limits `0` | No caps on archive contents; 100 is clamd's hard maximum nesting depth. |
| `MaxPartitions`, `MaxIconsPE`, `MaxRecHWP3`, `PCRE*MatchLimit` set very high | For these, `0` means literally zero (tested: `PCREMatchLimit 0` disables regex signatures), so they are raised instead. |

SSOT map (which file owns limits vs daemon caps): [docs/config-contract.md](../../docs/config-contract.md). Portable submission security: [docs/portable-submissions.md](../../docs/portable-submissions.md).

After editing this file:

```bash
docker compose up -d --force-recreate clamav
```

Wait until the container is **healthy** before running ingest.
