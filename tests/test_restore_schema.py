"""schema/014_restore.sql and scripts/restore_states.py list the same restore states."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import restore_states  # noqa: E402


def test_schema_states_match_restore_states():
    sql = (ROOT / "schema" / "014_restore.sql").read_text()
    for state in restore_states.OPEN + restore_states.FINISHED:
        assert f"'{state}'" in sql
    assert len(set(restore_states.OPEN + restore_states.FINISHED)) == 11
    index = sql.split("restore_request_one_open", 1)[1].split(";", 1)[0]
    assert all(f"'{s}'" in index for s in restore_states.OPEN)
    assert not any(f"'{s}'" in index for s in restore_states.FINISHED)


def test_schema_tiers_match_restore_states():
    sql = (ROOT / "schema" / "014_restore.sql").read_text()
    assert all(f"'{t}'" in sql for t in restore_states.TIERS)
