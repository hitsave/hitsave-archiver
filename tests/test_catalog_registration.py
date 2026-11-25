"""scripts/catalog_registration.py: note failed Omeka registrations in the ledger and retry them."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import catalog_registration as cr  # noqa: E402


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeConn:
    """Records SQL; answers advisory locks and the retry query from simple state."""

    def __init__(self, *, lock_free=True, due_rows=(), errors_after=None):
        self.calls = []
        self.lock_free = lock_free
        self.due_rows = list(due_rows)
        self.errors_after = errors_after or {}
        self.commits = 0
        self.closed = False
        self.autocommit = False

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if "pg_try_advisory_lock" in sql:
            return FakeResult([(self.lock_free,)])
        if "catalog_attempts >=" in sql:
            return FakeResult([(0,)])
        if "FROM game_ingest" in sql and "catalog_attempts <" in sql:
            return FakeResult(self.due_rows)
        if "SELECT catalog_error FROM game_ingest" in sql:
            return FakeResult([(self.errors_after.get(params[0]),)])
        return FakeResult([])

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True

    def sql(self, needle):
        return [c for c in self.calls if needle in c[0]]


# --- error_text --------------------------------------------------------------------------------


def test_error_text_redacts_omeka_keys():
    exc = RuntimeError("Max retries exceeded with url: /api/sites?key_identity=abc123&key_credential=s3cr3t")
    text = cr.error_text(exc)
    assert "abc123" not in text and "s3cr3t" not in text
    assert text.startswith("RuntimeError: ")
    assert "key_identity=REDACTED" in text


def test_error_text_uses_system_exit_message():
    assert cr.error_text(SystemExit("BagIt DIP not available")) == "SystemExit: BagIt DIP not available"


def test_error_text_is_capped():
    assert len(cr.error_text(RuntimeError("x" * 10_000))) == cr.MAX_ERROR_CHARS


# --- run_tracked -------------------------------------------------------------------------------


def make_register(*, raises=None, skip=False, claim=True, lock_free=True):
    """A stand-in for register-dip-omeka-api's body: claims the item, then succeeds or raises."""
    lock_conn = FakeConn(lock_free=lock_free)

    def register(ctx):
        if claim:
            got = cr.claim(ctx, lambda _db: lock_conn, game_key="nhl-hitz-2003-01a2",
                           config_path="/config/preservation/generated/repcopies-n-full/nhl-hitz-2003.yaml",
                           db_cfg={"host": "pg"})
            if not got:
                return
        if skip:
            ctx["skip"] = True
            return
        if raises:
            raise raises

    return register, lock_conn


def test_success_clears_the_error():
    conns = []
    register, lock_conn = make_register()
    cr.run_tracked(register, lambda _db: conns.append(FakeConn()) or conns[-1])
    (conn,) = conns
    (sql, params) = conn.sql("UPDATE game_ingest")[0]
    assert "catalog_error = NULL" in sql and "catalog_attempts = 0" in sql
    assert params[-1] == "nhl-hitz-2003-01a2"
    assert conn.commits == 1 and conn.closed
    assert lock_conn.closed


def test_failure_is_recorded_and_reraised():
    conns = []
    register, lock_conn = make_register(raises=RuntimeError("Network is unreachable"))
    with pytest.raises(RuntimeError):
        cr.run_tracked(register, lambda _db: conns.append(FakeConn()) or conns[-1])
    (conn,) = conns
    (sql, params) = conn.sql("UPDATE game_ingest")[0]
    assert "catalog_attempts = coalesce(catalog_attempts, 0) + 1" in sql
    assert params == ("RuntimeError: Network is unreachable",
                      "/config/preservation/generated/repcopies-n-full/nhl-hitz-2003.yaml",
                      "nhl-hitz-2003-01a2")
    assert conn.commits == 1
    assert lock_conn.closed


def test_system_exit_with_message_counts_as_failure():
    conns = []
    register, _ = make_register(raises=SystemExit("BagIt package not on Wasabi"))
    with pytest.raises(SystemExit):
        cr.run_tracked(register, lambda _db: conns.append(FakeConn()) or conns[-1])
    assert conns[0].sql("catalog_attempts = coalesce")


def test_clean_system_exit_counts_as_success():
    conns = []
    register, _ = make_register(raises=SystemExit(0))
    with pytest.raises(SystemExit):
        cr.run_tracked(register, lambda _db: conns.append(FakeConn()) or conns[-1])
    assert conns[0].sql("catalog_error = NULL")


def test_skip_records_nothing():
    conns = []
    register, _ = make_register(skip=True)
    cr.run_tracked(register, lambda _db: conns.append(FakeConn()) or conns[-1])
    assert conns == []


def test_failure_before_claim_records_nothing():
    conns = []
    register, _ = make_register(claim=False, raises=SystemExit("No ledger row"))
    with pytest.raises(SystemExit):
        cr.run_tracked(register, lambda _db: conns.append(FakeConn()) or conns[-1])
    assert conns == []


def test_item_locked_by_another_run_is_skipped(capsys):
    conns = []
    register, lock_conn = make_register(lock_free=False, raises=RuntimeError("must not run"))
    cr.run_tracked(register, lambda _db: conns.append(FakeConn()) or conns[-1])
    assert conns == []
    assert "another registration" in capsys.readouterr().out
    assert lock_conn.autocommit is True
    assert lock_conn.closed


def test_ledger_write_error_does_not_hide_the_registration_error(capsys):
    def broken(_db):
        raise OSError("ledger down")

    register, _ = make_register(raises=RuntimeError("Omeka down"))
    with pytest.raises(RuntimeError, match="Omeka down"):
        cr.run_tracked(register, broken)
    assert "could not note" in capsys.readouterr().out


def test_lock_key_is_per_item():
    conn = FakeConn()
    ctx = {}
    cr.claim(ctx, lambda _db: conn, game_key="a-01", config_path="/c.yaml", db_cfg={})
    (sql, params) = conn.sql("pg_try_advisory_lock")[0]
    assert "hashtext" in sql and params == ("catalog-register:a-01",)


# --- retry_failed ------------------------------------------------------------------------------

CFG = "/config/preservation/generated/repcopies-t-full/touch-battle-ninja.yaml"


def test_dry_run_lists_without_running():
    conn = FakeConn(due_rows=[("touch-battle-ninja-01a2", CFG, 1)])
    ran = []
    summary = cr.retry_failed(conn, ran.append, execute=False, exists=lambda p: True)
    assert ran == []
    assert summary["due"] == 1 and summary["retried"] == 0
    assert summary["items"] == [{"game_key": "touch-battle-ninja-01a2", "attempts": 1, "result": "would retry"}]


def test_retry_runs_registration_and_reads_the_outcome():
    conn = FakeConn(due_rows=[("ok-01", CFG, 1), ("bad-01", CFG, 3)],
                    errors_after={"ok-01": None, "bad-01": "RuntimeError: still down"})
    ran = []
    summary = cr.retry_failed(conn, lambda p: ran.append(p) or 0, execute=True, exists=lambda p: True)
    assert ran == [CFG, CFG]
    assert summary["retried"] == 2 and summary["registered"] == 1 and summary["still_failing"] == 1
    assert [i["result"] for i in summary["items"]] == ["registered", "failed: RuntimeError: still down"]


def test_missing_config_is_reported_not_run():
    conn = FakeConn(due_rows=[("gone-01", None, 1), ("moved-01", "/config/x.yaml", 1)])
    ran = []
    summary = cr.retry_failed(conn, ran.append, execute=True, exists=lambda p: False)
    assert ran == []
    assert summary["no_config"] == 2


def test_due_query_respects_attempt_cap_and_limit():
    conn = FakeConn()
    cr.retry_failed(conn, lambda p: 0, execute=True, max_attempts=10, limit=50)
    (sql, params) = conn.sql("catalog_attempts <")[0]
    assert "status = 'complete'" in sql and "catalog_error IS NOT NULL" in sql
    assert "ORDER BY catalog_attempted_at" in sql
    assert params == (10, 50)


def test_given_up_rows_are_counted():
    conn = FakeConn()
    conn.execute_orig = conn.execute

    def execute(sql, params=()):
        if "catalog_attempts >=" in sql:
            conn.calls.append((sql, params))
            return FakeResult([(2,)])
        return conn.execute_orig(sql, params)

    conn.execute = execute
    summary = cr.retry_failed(conn, lambda p: 0, execute=True, max_attempts=10)
    assert summary["given_up"] == 2


# --- schema and wiring -------------------------------------------------------------------------


def test_schema_adds_the_columns_and_marks_known_failures():
    sql = (ROOT / "schema" / "016_catalog_registration.sql").read_text()
    for col in ("catalog_error TEXT", "catalog_attempts INTEGER NOT NULL DEFAULT 0",
                "catalog_attempted_at TIMESTAMPTZ", "catalog_config_path TEXT"):
        assert f"ADD COLUMN IF NOT EXISTS {col}" in sql
    assert "omeka_item_id IS NULL" in sql and "status = 'complete'" in sql
    assert "catalog_error IS NULL" in sql  # rerunning the migration doesn't reset attempts


def test_migration_list_includes_016():
    sh = (ROOT / "scripts" / "apply-ledger-migrations.sh").read_text()
    assert "schema/016_catalog_registration.sql" in sh


def test_register_script_is_tracked():
    src = (ROOT / "scripts" / "register-dip-omeka-api.py").read_text()
    assert "run_tracked(" in src and "claim(" in src
