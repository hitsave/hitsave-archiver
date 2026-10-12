#!/usr/bin/env bash
# Apply SQL migrations after the first postgres init (initdb only runs 001–003 on empty volumes).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
USER="$(grep POSTGRES_USER config/preservation/generated.env | cut -d= -f2)"
DB="$(grep POSTGRES_DB config/preservation/generated.env | cut -d= -f2)"
for f in schema/003_wasabi_ledger.sql schema/004_bagit_ledger.sql schema/005_package_identity.sql schema/006_ingest_kind.sql schema/007_backfill_ingest_kind.sql schema/008_dump_storage.sql schema/009_backfill_ingest_kind_dump.sql; do
  docker compose exec -T postgres psql -U "$USER" -d "$DB" -f - < "$f"
  echo "Applied $f"
done
