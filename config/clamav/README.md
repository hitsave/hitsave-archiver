# ClamAV for preservation ingest

`clamd.conf` is bind-mounted into the `clamav` service. Settings under **Archives** and **Limits** affect both press-material and portable submission scans.

Portable submission security notes: [docs/portable-submissions.md](../../docs/portable-submissions.md).

After editing this file:

```bash
docker compose up -d --force-recreate clamav
```

Wait until the container is **healthy** before running ingest.
