"""package_lock.py: one writer at a time per package on Wasabi."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import package_lock as pl  # noqa: E402


class FakeConn:
    """Answers pg_try_advisory_lock from a list; records every query."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.queries: list[tuple[str, tuple]] = []
        self.closed = False

    def execute(self, sql, params=()):
        if self.closed:
            raise RuntimeError("the connection is closed")
        self.queries.append((sql, tuple(params)))
        value = self.answers.pop(0) if "pg_try_advisory_lock" in sql else True
        return type("Cur", (), {"fetchone": lambda _self: (value,)})()


def test_lock_ids_are_stable_and_namespaced():
    assert pl.lock_ids("pkg-a") == pl.lock_ids("pkg-a")
    assert pl.lock_ids("pkg-a") != pl.lock_ids("pkg-b")
    cls, obj = pl.lock_ids("pkg-a")
    assert cls == pl.LOCK_CLASS
    assert -2**31 <= obj < 2**31


def test_takes_the_lock_and_releases_it_after_the_block():
    conn = FakeConn([True])
    with pl.package_lock(conn, "pkg-a"):
        assert len(conn.queries) == 1
    take, release = conn.queries
    assert "pg_try_advisory_lock" in take[0] and take[1] == pl.lock_ids("pkg-a")
    assert "pg_advisory_unlock" in release[0] and release[1] == pl.lock_ids("pkg-a")


def test_releases_the_lock_when_the_block_raises():
    conn = FakeConn([True])
    with pytest.raises(ValueError):
        with pl.package_lock(conn, "pkg-a"):
            raise ValueError("boom")
    assert "pg_advisory_unlock" in conn.queries[-1][0]


def test_waits_while_another_job_holds_the_lock():
    conn = FakeConn([False, False, True])
    slept: list[float] = []
    with pl.package_lock(conn, "pkg-a", sleep=slept.append, clock=lambda: 0.0):
        pass
    assert slept == [pl.POLL_SECONDS, pl.POLL_SECONDS]


def test_gives_up_after_the_wait_and_says_which_package():
    conn = FakeConn([False] * 10)
    t = iter(range(0, 1000, 300))
    with pytest.raises(pl.PackageBusy, match="pkg-a"):
        with pl.package_lock(conn, "pkg-a", wait_seconds=600, sleep=lambda s: None, clock=lambda: next(t)):
            pytest.fail("ran without the lock")
    assert not any("pg_advisory_unlock" in q for q, _ in conn.queries)


def test_lost_connection_keeps_the_original_error():
    conn = FakeConn([True])
    with pytest.raises(ValueError, match="upload failed"):
        with pl.package_lock(conn, "pkg-a"):
            conn.closed = True  # Postgres released the lock with the session
            raise ValueError("upload failed")
