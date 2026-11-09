"""Registration holds a dump whose Redump disc already has a catalog item."""
from __future__ import annotations

import sys

import pytest

import apply_dump_catalog_omeka as apply
from dump_catalog_match import HOLD_REASON


class FakeCursor:
    def __init__(self, log):
        self.log, self.rowcount = log, 1

    def execute(self, sql, params):
        self.log.append((" ".join(sql.split()), params))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConn(FakeCursor):
    def cursor(self):
        return FakeCursor(self.log)

    def commit(self):
        pass


@pytest.fixture
def ledger(monkeypatch):
    log: list = []
    monkeypatch.setattr(apply, "load_ingest_config", lambda: {"database": {"config_file": "x"}})
    monkeypatch.setattr(apply, "load_yaml", lambda path: {"postgres": {}})
    monkeypatch.setattr(apply, "pg_connect", lambda cfg: FakeConn(log))
    return log


def test_hold_sets_reason_and_note(ledger):
    apply.hold_for_review("k", "Not registered in Omeka: item 16396 …")
    sql, params = ledger[0]
    assert sql.startswith("UPDATE game_ingest SET block_reason = %s, error_message = %s")
    assert params == (HOLD_REASON, "Not registered in Omeka: item 16396 …", "k")


def test_registering_later_clears_only_this_hold(ledger):
    apply.update_ledger_omeka("k", 31466)
    sql, params = ledger[0]
    assert "block_reason = CASE WHEN block_reason = %s THEN NULL ELSE block_reason END" in sql
    assert "error_message = CASE WHEN block_reason = %s THEN NULL ELSE error_message END" in sql
    assert params == (31466, HOLD_REASON, HOLD_REASON, "k")


def test_main_exits_3_when_held(monkeypatch, tmp_path, capsys):
    cfg = tmp_path / "game.yaml"
    cfg.write_text("ingest_kind: dump\ngame_key: k\nsource_game_folder: /x\n")
    monkeypatch.setattr(apply, "resolve_game_config", lambda c, **kw: c)
    monkeypatch.setattr(apply, "load_yaml", lambda p: {"game_key": "k"})
    monkeypatch.setattr(apply, "apply_dump_catalog", lambda **kw: {"game_key": "k", "held": True, "reason": "r"})
    monkeypatch.setattr(sys, "argv", ["apply", str(cfg)])
    with pytest.raises(SystemExit) as exc:
        apply.main()
    assert exc.value.code == apply.HELD_EXIT == 3
    assert '"held": true' in capsys.readouterr().out
