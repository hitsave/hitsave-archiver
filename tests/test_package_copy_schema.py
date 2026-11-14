"""Ledger table for copies of packages (schema/013_package_copy.sql) and the CRC64NVME dependency."""
from __future__ import annotations

import base64
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "schema" / "013_package_copy.sql"


def test_migration_creates_package_copy_once_per_location():
    sql = MIGRATION.read_text()
    assert re.search(r"CREATE TABLE IF NOT EXISTS package_copy", sql)
    assert "REFERENCES game_ingest" in sql and "ON DELETE CASCADE" in sql
    assert re.search(r"UNIQUE\s*\(\s*game_ingest_id\s*,\s*location\s*\)", sql)
    for column in ("bucket", "prefix", "object_count", "bytes", "tagmanifest_sha256", "copied_at", "verified_at"):
        assert re.search(rf"\b{column}\b", sql), column


def test_migration_is_applied_by_the_migration_script():
    assert "schema/013_package_copy.sql" in (ROOT / "scripts" / "apply-ledger-migrations.sh").read_text()


def test_awscrt_is_a_requirement():
    reqs = (ROOT / "ingest-worker" / "requirements.txt").read_text()
    assert re.search(r"^awscrt>=", reqs, re.M)


def test_botocore_can_compute_crc64nvme():
    from botocore.httpchecksum import CrtCrc64NvmeChecksum

    c = CrtCrc64NvmeChecksum()
    c.update(b"123456789")
    # CRC-64/NVME check value for "123456789".
    assert c.digest() == bytes.fromhex("ae8b14860a799888")
    assert base64.b64decode(c.b64digest()) == bytes.fromhex("ae8b14860a799888")
