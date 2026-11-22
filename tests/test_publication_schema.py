"""schema/015_publication.sql: one publication row per Omeka item, and the migration list."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_publication_schema():
    sql = (ROOT / "schema" / "015_publication.sql").read_text()
    assert "omeka_item_id INTEGER NOT NULL UNIQUE" in sql
    for s in ("'ledger'", "'reconcile'", "'backfill'"):
        assert s in sql
    assert "WHERE announced_at IS NULL" in sql
    assert "CREATE TABLE IF NOT EXISTS digest_state" in sql


def test_migration_list_includes_014_and_015():
    sh = (ROOT / "scripts" / "apply-ledger-migrations.sh").read_text()
    assert "schema/014_restore.sql" in sh and "schema/015_publication.sql" in sh
