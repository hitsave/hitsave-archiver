#!/usr/bin/env bash
# Apply SQL migrations after the first postgres init (initdb only runs 001–003 on empty volumes).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
docker compose exec -T postgres psql -U "$(grep POSTGRES_USER config/preservation/generated.env | cut -d= -f2)" \
  -d "$(grep POSTGRES_DB config/preservation/generated.env | cut -d= -f2)" \
  -f - < schema/003_wasabi_ledger.sql
echo "Applied schema/003_wasabi_ledger.sql"
