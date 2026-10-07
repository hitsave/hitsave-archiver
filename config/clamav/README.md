# ClamAV for preservation ingest

`clamd.conf` is bind-mounted into the `clamav` service. It is intentionally **small**: the image ships a full template, but we only commit the directives we override.

| Setting | Why |
|---------|-----|
| `TCPSocket 3310` | `ingest-worker` uses `pyclamd` over the Compose network (`clamav:3310`). |
| `MaxFiles` / `MaxRecursion` | Align with `config/preservation/submissions.yaml` → `limits.max_archive_members` (50000) and archive depth. |
| `MaxFileSize`, `MaxScanSize`, `StreamMaxLength`, `PCREMaxFileSize` | 512 MiB cap for large press videos; same order of magnitude as `limits.max_member_bytes`. |

SSOT map (which file owns limits vs daemon caps): [docs/config-contract.md](../../docs/config-contract.md). Portable submission security: [docs/portable-submissions.md](../../docs/portable-submissions.md).

After editing this file:

```bash
docker compose up -d --force-recreate clamav
```

Wait until the container is **healthy** before running ingest.
