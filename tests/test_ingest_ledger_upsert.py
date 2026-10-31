"""ingest-folder.py ledger writes: status transitions must not erase package location/size fields,
and a MobyGames block must stop ingest before anything is bagged or uploaded."""
from __future__ import annotations

import importlib.util
import inspect
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("ingest_folder", ROOT / "scripts" / "ingest-folder.py")
ingest_folder = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ingest_folder)

COLUMNS = (
    "game_key, source_path, status, dip_path, aip_bag_path, package_uuid, file_count, total_bytes, "
    "checksum_manifest_path, error_message, package_format, submission_source_id, inventory_json, "
    "sip_fingerprint, package_id, title_slug, sort_letter, ingest_kind, storage_layout, "
    "package_storage_prefix, dump_class, dump_system, dump_category, updated_at"
)


class _SqliteCursor:
    """Runs the real upsert SQL on SQLite (translating the few Postgres-only bits)."""

    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        sql = sql.replace("%s", "?").replace("::jsonb", "").replace("NOW()", "CURRENT_TIMESTAMP")
        self.db.execute(sql, params)


class SqliteConn:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        cols = ", ".join(f"{c.strip()} TEXT" for c in COLUMNS.split(","))
        self.db.execute(f"CREATE TABLE game_ingest ({cols}, PRIMARY KEY (game_key))")

    def cursor(self):
        return _SqliteCursor(self.db)

    def commit(self):
        self.db.commit()

    def row(self, key):
        cur = self.db.execute("SELECT * FROM game_ingest WHERE game_key = ?", (key,))
        names = [d[0] for d in cur.description]
        return dict(zip(names, cur.fetchone()))


COMPLETE = dict(
    game_key="pkg-1",
    source_path="/src/pkg",
    status="complete",
    dip_path="/out/bag/pkg-1",
    aip_bag_path="/out/bag/pkg-1",
    package_uuid="uuid-1",
    file_count=12,
    total_bytes=4096,
    checksum_manifest_path="/out/bag/pkg-1/manifest-sha256.txt",
    error_message=None,
    package_format="bagit",
)

KEPT = ("dip_path", "aip_bag_path", "file_count", "total_bytes", "checksum_manifest_path", "package_uuid")


@pytest.mark.parametrize("status", ["registered", "scanning", "building_bagit", "uploading_package"])
def test_intermediate_status_keeps_package_fields(status):
    conn = SqliteConn()
    ingest_folder.upsert_ledger(conn, **COMPLETE)
    ingest_folder.upsert_ledger(conn, game_key="pkg-1", source_path="/src/pkg", status=status)
    row = conn.row("pkg-1")
    assert row["status"] == status
    for col in KEPT:
        assert row[col] is not None and str(row[col]) == str(COMPLETE[col]), col


def test_failed_rerun_keeps_fields_and_records_error():
    conn = SqliteConn()
    ingest_folder.upsert_ledger(conn, **COMPLETE)
    ingest_folder.upsert_ledger(
        conn, game_key="pkg-1", source_path="/src/pkg", status="failed", error_message="boom"
    )
    row = conn.row("pkg-1")
    assert row["status"] == "failed"
    assert row["error_message"] == "boom"
    for col in KEPT:
        assert str(row[col]) == str(COMPLETE[col]), col


def test_success_after_failure_updates_fields_and_clears_error():
    conn = SqliteConn()
    ingest_folder.upsert_ledger(conn, **COMPLETE)
    ingest_folder.upsert_ledger(
        conn, game_key="pkg-1", source_path="/src/pkg", status="failed", error_message="boom"
    )
    newer = {**COMPLETE, "file_count": 13, "total_bytes": 5000}
    ingest_folder.upsert_ledger(conn, **newer)
    row = conn.row("pkg-1")
    assert row["status"] == "complete"
    assert row["error_message"] is None
    assert int(row["file_count"]) == 13 and int(row["total_bytes"]) == 5000


def test_moby_gate_raises_on_block(monkeypatch):
    monkeypatch.setattr(ingest_folder, "moby_enrich_enabled", lambda cfg: True)
    monkeypatch.setattr(ingest_folder.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=2))
    with pytest.raises(ingest_folder.MobyBlockedError):
        ingest_folder.run_moby_gate({}, Path("/tmp/game.yml"))


def test_moby_gate_passes_and_skips(monkeypatch):
    calls = []
    monkeypatch.setattr(
        ingest_folder.subprocess, "run", lambda *a, **k: calls.append(a) or SimpleNamespace(returncode=0)
    )
    monkeypatch.setattr(ingest_folder, "moby_enrich_enabled", lambda cfg: True)
    ingest_folder.run_moby_gate({}, Path("/tmp/game.yml"))
    monkeypatch.setattr(ingest_folder, "moby_enrich_enabled", lambda cfg: False)
    ingest_folder.run_moby_gate({}, Path("/tmp/game.yml"))
    assert len(calls) == 1


def test_moby_gate_runs_before_scan_bag_and_upload():
    src = inspect.getsource(ingest_folder.main)
    gate = src.index("run_moby_gate(")
    assert gate < src.index("scan_sip_tree(")
    assert gate < src.index("run_bagit_ingest(")
    assert "moby_enrich_game.py" not in inspect.getsource(ingest_folder.run_bagit_ingest)
