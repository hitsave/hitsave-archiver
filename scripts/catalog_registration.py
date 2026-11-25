#!/usr/bin/env python3
"""Note failed Omeka registrations in the ledger, and retry them (docs/catalog-registration-retry.md).

register-dip-omeka-api.py runs its work through run_tracked(): a failure sets
game_ingest.catalog_error and counts an attempt, and a success clears them. claim() takes a
per-item advisory lock, so a retry and a batch can't register the same item at once.

Run as a script, it retries the rows that have an error, oldest attempt first:

    catalog_registration.py              # dry run: list what would be retried
    catalog_registration.py --execute    # rerun registration for each
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from omeka_key_redaction import redact_omeka_keys  # noqa: E402

MAX_ERROR_CHARS = 2000
MAX_ATTEMPTS = 10
RETRY_LIMIT = 50
REGISTER_SCRIPT = Path(__file__).resolve().parent / "register-dip-omeka-api.py"


def error_text(exc: BaseException) -> str:
    """One line for the ledger: exception type and message, Omeka keys removed, capped."""
    msg = exc.code if isinstance(exc, SystemExit) else exc
    return redact_omeka_keys(f"{type(exc).__name__}: {msg}")[:MAX_ERROR_CHARS]


def claim(ctx: dict, connect: Callable, *, game_key: str, config_path: str, db_cfg: dict) -> bool:
    """Remember which item this run registers and lock it. False: another run holds the item."""
    conn = connect(db_cfg)
    conn.autocommit = True  # the session lock outlives any transaction; don't sit idle in one
    ctx["lock_conn"] = conn
    got = bool(conn.execute("SELECT pg_try_advisory_lock(hashtext(%s))",
                            (f"catalog-register:{game_key}",)).fetchone()[0])
    if not got:
        print(json.dumps({"game_key": game_key, "skipped": True,
                          "reason": "another registration of this item is running"}, indent=2))
        ctx["skip"] = True
        return False
    ctx.update(game_key=game_key, config_path=config_path, db_cfg=db_cfg)
    return True


def record_failure(conn, game_key: str, error: str, config_path: str | None) -> None:
    conn.execute(
        "UPDATE game_ingest SET catalog_error = %s, catalog_attempts = coalesce(catalog_attempts, 0) + 1, "
        "catalog_attempted_at = now(), catalog_config_path = coalesce(%s, catalog_config_path) "
        "WHERE game_key = %s",
        (error, config_path, game_key),
    )
    conn.commit()


def record_success(conn, game_key: str) -> None:
    conn.execute(
        "UPDATE game_ingest SET catalog_error = NULL, catalog_attempts = 0, catalog_attempted_at = now() "
        "WHERE game_key = %s",
        (game_key,),
    )
    conn.commit()


def _note(ctx: dict, connect: Callable, exc: BaseException | None) -> None:
    if ctx.get("skip") or not ctx.get("game_key"):
        return
    try:
        conn = connect(ctx["db_cfg"])
        try:
            if exc is None:
                record_success(conn, ctx["game_key"])
            else:
                record_failure(conn, ctx["game_key"], error_text(exc), ctx.get("config_path"))
        finally:
            conn.close()
    except Exception as e:  # never hide the registration's own outcome
        print(f"WARNING: could not note the registration outcome for {ctx['game_key']} in the ledger: "
              f"{redact_omeka_keys(str(e))}", flush=True)


def run_tracked(register: Callable[[dict], None], connect: Callable) -> None:
    """Run register(ctx) and note its outcome in the ledger once it has claimed an item."""
    ctx: dict = {}
    try:
        register(ctx)
    except SystemExit as exc:
        _note(ctx, connect, None if exc.code in (0, None) else exc)
        raise
    except Exception as exc:
        _note(ctx, connect, exc)
        raise
    else:
        _note(ctx, connect, None)
    finally:
        lock = ctx.get("lock_conn")
        if lock is not None:
            try:
                lock.close()
            except Exception:
                pass


def retry_failed(conn, register: Callable[[str], int], *, execute: bool, max_attempts: int = MAX_ATTEMPTS,
                 limit: int = RETRY_LIMIT, exists: Callable[[str], bool] = os.path.exists) -> dict:
    """Rerun registration for complete rows with an error. register(config_path) returns an exit code."""
    rows = conn.execute(
        "SELECT game_key, catalog_config_path, catalog_attempts FROM game_ingest "
        "WHERE status = 'complete' AND catalog_error IS NOT NULL AND catalog_attempts < %s "
        "ORDER BY catalog_attempted_at NULLS FIRST, id LIMIT %s",
        (max_attempts, limit),
    ).fetchall()
    given_up = conn.execute(
        "SELECT count(*) FROM game_ingest WHERE status = 'complete' AND catalog_error IS NOT NULL "
        "AND catalog_attempts >= %s",
        (max_attempts,),
    ).fetchone()[0]
    summary = {"execute": execute, "due": len(rows), "retried": 0, "registered": 0, "still_failing": 0,
               "no_config": 0, "given_up": int(given_up or 0), "items": []}
    for game_key, config_path, attempts in rows:
        item = {"game_key": game_key, "attempts": int(attempts or 0)}
        summary["items"].append(item)
        if not config_path or not exists(config_path):
            summary["no_config"] += 1
            item["result"] = f"no config file ({config_path or 'none recorded'}); register it by hand"
            continue
        if not execute:
            item["result"] = "would retry"
            continue
        summary["retried"] += 1
        register(config_path)
        error = conn.execute("SELECT catalog_error FROM game_ingest WHERE game_key = %s",
                             (game_key,)).fetchone()[0]
        if error is None:
            summary["registered"] += 1
            item["result"] = "registered"
        else:
            summary["still_failing"] += 1
            item["result"] = f"failed: {error}"
    return summary


def _register_subprocess(config_path: str) -> int:
    return subprocess.run([sys.executable, str(REGISTER_SCRIPT), config_path], check=False).returncode


def main() -> int:
    import psycopg

    from yaml_config import load_yaml

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--execute", action="store_true", help="rerun registration (default: dry run)")
    ap.add_argument("--max-attempts", type=int, default=MAX_ATTEMPTS,
                    help="leave rows alone once they have failed this many times")
    ap.add_argument("--limit", type=int, default=RETRY_LIMIT, help="retry at most this many rows per run")
    args = ap.parse_args()

    config_root = Path(os.environ.get("HITSAVE_CONFIG_ROOT", "/config"))
    ingest = load_yaml(config_root / "preservation" / "ingest.yaml")
    db = load_yaml(Path(ingest["database"]["config_file"]))["postgres"]
    conn = psycopg.connect(host=db["host"], port=int(db.get("port", 5432)), dbname=db["database"],
                           user=db["user"], password=db["password"], autocommit=True)
    try:
        summary = retry_failed(conn, _register_subprocess, execute=args.execute,
                               max_attempts=args.max_attempts, limit=args.limit)
    finally:
        conn.close()
    print(json.dumps(summary, indent=2), flush=True)
    # Fail the job while anything needs a person, so Windmill shows it.
    return 1 if summary["still_failing"] or summary["no_config"] or summary["given_up"] else 0


if __name__ == "__main__":
    sys.exit(main())
