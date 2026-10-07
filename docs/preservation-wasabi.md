# Wasabi AIP upload (Phase 3P-b)

**Packaging standard:** [preservation-packaging-standard.md](preservation-packaging-standard.md) — **E-ARK AIP (CSIP)** on Wasabi as an **unpacked package root** (`METS.xml`, representations, etc.) under one prefix per game.

**Note:** Wasabi stores **unpacked E-ARK AIP** package roots (`METS.xml` at prefix root). Legacy BagIt-only objects may remain in the bucket from early pilots; new ingests use `build-aip-e-ark.py`.

DIPs stay local (or on Omeka); only AIPs go to Wasabi.

## Setup

1. Create a **test bucket** in Wasabi (same region as `config/preservation/ingest.yaml`, default `us-east-1`).
2. Create an access key limited to that bucket.
3. Copy credentials into the **private** repo (`hitsave-archiver-config`):

   ```bash
   cp config/secrets/wasabi-credentials.yaml.example ../hitsave-archiver-config/secrets/wasabi-credentials.yaml
   # edit access_key_id and secret_access_key
   ```

4. Edit `config/preservation/ingest.yaml`:

   - `wasabi.bucket` — your test bucket name
   - `wasabi.prefix` — optional folder prefix (default `press-material/`)
   - `wasabi.enabled: true`

5. If Postgres already existed before `schema/003_wasabi_ledger.sql`, apply the migration:

   ```bash
   bash scripts/apply-ledger-migrations.sh
   ```

6. Rebuild workers after code changes:

   ```bash
   docker compose build ingest-worker status-web
   ```

## Test upload only

After an E-ARK AIP exists under `/output/aip/<game_key>/` (unpacked package root with `METS.xml`):

```bash
docker compose run --rm ingest-worker \
  python /app/scripts/upload-aip-wasabi.py \
  /output/aip/YOUR_AIP_DIR YOUR_GAME_KEY
```

Requires a `game_ingest` row for `YOUR_GAME_KEY` (created by a prior ingest run).

Each object is **SHA-256 verified** after upload when `wasabi.verify_upload: true`.

## Full ingest

With `wasabi.enabled: true`, `ingest-game-folder.py` runs ClamAV → E-ARK AIP + DIP → **Wasabi upload** (AIP) → Omeka DIP → Moby enrich → ledger `complete`. Ledger fields: `aip_wasabi_prefix`, `aip_uploaded_at`.
