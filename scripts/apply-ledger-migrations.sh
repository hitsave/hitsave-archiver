#!/usr/bin/env bash
# Apply SQL migrations after the first postgres init (initdb only runs 001–003 on empty volumes).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
USER="$(grep POSTGRES_USER config/preservation/generated.env | cut -d= -f2)"
DB="$(grep POSTGRES_DB config/preservation/generated.env | cut -d= -f2)"
for f in schema/003_wasabi_ledger.sql schema/004_bagit_ledger.sql schema/005_package_identity.sql schema/006_ingest_kind.sql schema/007_backfill_ingest_kind.sql schema/008_dump_storage.sql schema/009_backfill_ingest_kind_dump.sql schema/010_intake_rejection.sql schema/011_intake_source.sql schema/012_disc_contents.sql schema/013_package_copy.sql; do
  docker compose run --rm -T psql -U "$USER" -d "$DB" -f - < "$f"
  echo "Applied $f"
done
