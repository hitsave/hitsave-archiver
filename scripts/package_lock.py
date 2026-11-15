"""One writer at a time per package on Wasabi.

Several jobs add tag files to bags that are already on Wasabi: the daily catalog snapshot, a
catalog correction and a Glacier restore. Each one reads tagmanifest-sha256.txt, adds its lines
and writes the file back. If two of them do that to the same package at once, the second write
drops the first job's lines.

Each of those jobs takes this lock before it reads the tag manifest and holds it until its last
write. The lock is a Postgres advisory lock in the ledger database, keyed by the package's
game_key. It belongs to the database session, so it outlives commits and rollbacks on the same
connection, and Postgres releases it if the job dies.
"""
from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

# First key of the two-key advisory lock, so these locks never clash with other users of
# single-key advisory locks in the same database. The bytes spell "HSPK".
LOCK_CLASS = 0x4853504B
POLL_SECONDS = 2.0
WAIT_SECONDS = 600.0


class PackageBusy(RuntimeError):
    """Another job held the package's lock for longer than the wait."""


def lock_ids(game_key: str) -> tuple[int, int]:
    """(class, object) keys for pg_try_advisory_lock(int, int)."""
    obj = int.from_bytes(hashlib.sha256(game_key.encode("utf-8")).digest()[:4], "big", signed=True)
    return LOCK_CLASS, obj


@contextmanager
def package_lock(conn, game_key: str, *, wait_seconds: float = WAIT_SECONDS,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> Iterator[None]:
    """Hold the write lock for *game_key* on *conn* for the length of the block."""
    ids = lock_ids(game_key)
    deadline = clock() + wait_seconds
    while not conn.execute("SELECT pg_try_advisory_lock(%s, %s)", ids).fetchone()[0]:
        if clock() >= deadline:
            raise PackageBusy(f"another job is writing to package {game_key}; try again later")
        sleep(POLL_SECONDS)
    try:
        yield
    finally:
        if not conn.closed:  # a closed session has already released the lock
            conn.execute("SELECT pg_advisory_unlock(%s, %s)", ids)


@contextmanager
def ledger_lock(game_key: str, ingest_config: Path = Path("/config/preservation/ingest.yaml")) -> Iterator[None]:
    """package_lock on a connection of its own, to the ledger named in ingest.yaml."""
    import psycopg

    from yaml_config import load_yaml

    db = load_yaml(Path(load_yaml(ingest_config)["database"]["config_file"]))["postgres"]
    with psycopg.connect(host=db["host"], port=int(db.get("port", 5432)), dbname=db["database"],
                         user=db["user"], password=db["password"], autocommit=True) as conn:
        with package_lock(conn, game_key):
            yield
