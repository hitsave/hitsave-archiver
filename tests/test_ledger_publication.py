"""ledger/publication.py: record the first publish of an Omeka item."""
from __future__ import annotations

import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ledger"))

from publication import record_published  # noqa: E402


class FakeCursor:
    def __init__(self, rowcount):
        self.rowcount = rowcount


class FakeConn:
    def __init__(self, rowcount):
        self.rowcount = rowcount
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return FakeCursor(self.rowcount)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_inserts_once():
    conn = FakeConn(1)
    assert record_published(lambda: conn, 31336, "editor@example.org") is True
    sql, params = conn.calls[0]
    assert "ON CONFLICT (omeka_item_id) DO NOTHING" in sql
    assert "FROM game_ingest WHERE omeka_item_id" in sql
    assert params == (31336, 31336, "editor@example.org", "ledger")


def test_repeat_is_noop():
    assert record_published(lambda: FakeConn(0), 31336, "editor@example.org") is False


def test_db_error_returns_false(capsys):
    def boom():
        raise psycopg.OperationalError("down")

    assert record_published(boom, 31336, "editor@example.org") is False
    assert "31336" in capsys.readouterr().out
