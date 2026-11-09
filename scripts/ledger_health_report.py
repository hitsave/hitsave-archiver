#!/usr/bin/env python3
"""
Read-only preservation ledger report for operators.

Windmill worker and SSH: `./scripts/run-ledger-health-report.sh` (direct psycopg, no container).
"""
#requirements:
#psycopg[binary]
#pyyaml
from __future__ import annotations

import argparse
import json
import re
import os
import sys
from pathlib import Path
from typing import Any

try:
    import psycopg
    import yaml
except ImportError as e:
    print(e, file=sys.stderr)
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from damaged_listing import DAMAGED_SQL  # noqa: E402

# --- Queries (visible in Windmill script editor and echoed to run logs) ---

SUMMARY_BY_STATUS_SQL = """
SELECT status, COUNT(*)::int AS n
FROM game_ingest
GROUP BY status
ORDER BY status
"""

# Named row conditions shared by the health counts, the attention queue and the ledger app's
# ?flag= filter, so a count and the rows behind it can't drift apart. name -> (SQL, label).
# A quarantine is a decision already made (its note lives in error_message), so quarantined
# packages never count as errors or as needing attention; the status chip lists them.
NOT_QUARANTINED = "status <> 'quarantine'"
FLAG_CONDITIONS: dict[str, tuple[str, str]] = {
    "failed_or_error": (
        f"({NOT_QUARANTINED} AND (status = 'failed' OR (error_message IS NOT NULL AND error_message <> '')))",
        "failed or has an error",
    ),
    "no_omeka": (
        "(omeka_item_id IS NULL AND status = 'complete')",
        "complete, no catalog record",
    ),
    "attention": (
        f"({NOT_QUARANTINED} AND (status = 'failed'"
        " OR (status = 'complete' AND omeka_item_id IS NULL)"
        " OR (error_message IS NOT NULL AND error_message <> '')))",
        "needs attention",
    ),
    "damaged": (DAMAGED_SQL, "has damaged files"),
}

FAILED_OR_ERROR_SQL = f"""
SELECT COUNT(*)::int FROM game_ingest
WHERE {FLAG_CONDITIONS["failed_or_error"][0]}
"""

COMPLETE_MISSING_OMEKA_SQL = f"""
SELECT COUNT(*)::int FROM game_ingest
WHERE {FLAG_CONDITIONS["no_omeka"][0]}
"""

DAMAGED_COUNT_SQL = f"""
SELECT COUNT(*)::int FROM game_ingest
WHERE {FLAG_CONDITIONS["damaged"][0]}
"""

LEDGER_SORT_COLUMNS: dict[str, str] = {
    "game_key": "game_key",
    "status": "status",
    "moby_status": "moby_status",
    "moby_title": "moby_title",
    "omeka_item_id": "omeka_item_id",
    "error_message": "error_message",
    "block_reason": "block_reason",
    "updated_at": "updated_at",
}

# Word search: every word of the query must start a word somewhere in the row (any field, any order).
# Both sides are lowercased and punctuation (dashes, slashes, underscores, …) becomes a space, so
# "Famitsu Wave DVD 2008-12" matches game_key "famitsu-wave-dvd-2008-12-gatsugou-…".
SEARCH_FIELDS = (
    "game_key", "source_path", "moby_title", "status", "moby_status", "block_reason",
    "error_message", "ingest_kind", "aip_wasabi_prefix", "package_storage_prefix", "dump_class",
    "dump_system", "dump_category", "moby_game_id::text", "omeka_item_id::text", "omeka_media_id::text",
)
SEARCH_HAYSTACK = (
    "(' ' || regexp_replace(lower(concat_ws(' ', " + ", ".join(SEARCH_FIELDS) + ")), "
    "'[^[:alnum:]]+', ' ', 'g') || ' ')"
)
SEARCH_COLUMNS = """
SELECT game_key, status, ingest_kind, moby_status, block_reason,
       omeka_item_id, omeka_media_id, moby_title, moby_game_id, source_path,
       error_message, aip_wasabi_prefix, package_storage_prefix,
       dump_class, dump_system, dump_category, updated_at
FROM game_ingest
"""


def search_terms(query: str) -> list[str]:
    """Lowercased words of the query, split on anything that isn't a letter or digit."""
    return [t for t in re.split(r"[\W_]+", (query or "").lower()) if t]


def search_where(query: str) -> tuple[str, tuple[str, ...]]:
    """(SQL condition, binds): every term starts a word of the normalized row text.

    Prefix-of-a-word matching keeps short terms precise ("12" must not match inside a uuid such
    as 01a1123...), while "famit" still finds "famitsu".
    """
    terms = search_terms(query)
    if not terms:
        return "TRUE", ()
    cond = " AND ".join(f"{SEARCH_HAYSTACK} LIKE %s" for _ in terms)
    return cond, tuple(f"% {t}%" for t in terms)
def ledger_where(query: str, *, status: str = "", flag: str = "") -> tuple[str, tuple[str, ...]]:
    """Search terms AND an exact status AND a named flag (unknown flags are ignored)."""
    parts: list[str] = []
    binds: tuple[str, ...] = ()
    cond, terms = search_where(query)
    if terms:
        parts.append(cond)
        binds += terms
    if status:
        parts.append("status = %s")
        binds += (status,)
    if flag in FLAG_CONDITIONS:
        parts.append(FLAG_CONDITIONS[flag][0])
    return (" AND ".join(parts) if parts else "TRUE"), binds


LEDGER_COUNT_SQL = "SELECT COUNT(*)::int FROM game_ingest"

ATTENTION_SELECT = f"""
SELECT game_key, status, ingest_kind, moby_status, block_reason,
       omeka_item_id, moby_title, source_path, error_message, updated_at
FROM game_ingest
WHERE {FLAG_CONDITIONS["attention"][0]}
"""

# Windmill / docs: full attention query (default sort).
ATTENTION_SQL = ATTENTION_SELECT.strip() + "\nORDER BY updated_at DESC\nLIMIT %s"


def normalize_ledger_sort(sort: str | None, *, default: str = "updated_at") -> str:
    key = (sort or "").strip()
    if key in LEDGER_SORT_COLUMNS:
        return LEDGER_SORT_COLUMNS[key]
    allowed = set(LEDGER_SORT_COLUMNS.values())
    if key in allowed:
        return key
    return default


def normalize_ledger_sort_dir(direction: str | None, *, default: str = "desc") -> str:
    d = (direction or "").strip().lower()
    if d == "asc":
        return "ASC"
    if d == "desc":
        return "DESC"
    return "DESC" if default.lower() == "desc" else "ASC"


def ledger_order_sql(sort_col: str, sort_dir: str) -> str:
    col = normalize_ledger_sort(sort_col)
    direction = normalize_ledger_sort_dir(sort_dir)
    nulls = "NULLS LAST" if direction == "ASC" else "NULLS FIRST"
    return f"ORDER BY {col} {direction} {nulls}"


def pg_connect():
    """Preservation ledger Postgres (database.yaml — not a separate ops database)."""
    ingest_db = Path("/config/preservation/database.yaml")
    if ingest_db.is_file():
        REPO_ROOT = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        from batch_ledger import pg_connect as batch_pg_connect  # noqa: E402

        return batch_pg_connect()

    private = Path(os.environ.get("HITSAVE_PRIVATE_CONFIG", "/config/private"))
    db_path = private / "preservation" / "database.yaml"
    if not db_path.is_file():
        raise FileNotFoundError(
            f"Missing {db_path}; set HITSAVE_PRIVATE_CONFIG to hitsave-archiver-config"
        )
    db = yaml.safe_load(db_path.read_text(encoding="utf-8"))["postgres"]
    return psycopg.connect(
        host=db["host"],
        port=int(db.get("port", 5432)),
        dbname=db["database"],
        user=db["user"],
        password=db["password"],
    )


def _row_dict(cur, row: tuple) -> dict[str, Any]:
    cols = [d[0] for d in cur.description]
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in zip(cols, row)}


def ledger_summary(conn) -> dict[str, Any]:
    with conn.cursor() as cur:
        print("Query: summary by status\n", SUMMARY_BY_STATUS_SQL.strip(), sep="")
        cur.execute(SUMMARY_BY_STATUS_SQL)
        by_status = {row[0]: row[1] for row in cur.fetchall()}
        print("Query: failed or non-empty error_message\n", FAILED_OR_ERROR_SQL.strip(), sep="")
        cur.execute(FAILED_OR_ERROR_SQL)
        failed_or_error = cur.fetchone()[0]
        print(
            "Query: complete rows missing omeka_item_id\n",
            COMPLETE_MISSING_OMEKA_SQL.strip(),
            sep="",
        )
        cur.execute(COMPLETE_MISSING_OMEKA_SQL)
        complete_no_omeka = cur.fetchone()[0]
        print("Query: rows with damaged files\n", DAMAGED_COUNT_SQL.strip(), sep="")
        cur.execute(DAMAGED_COUNT_SQL)
        damaged = cur.fetchone()[0]
    return {
        "by_status": by_status,
        "failed_or_error_message": failed_or_error,
        "complete_missing_omeka_item_id": complete_no_omeka,
        "damaged_files": damaged,
    }


def ledger_search_count(conn, query: str, *, status: str = "", flag: str = "") -> int:
    cond, binds = ledger_where(query, status=status, flag=flag)
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*)::int FROM game_ingest WHERE {cond}", binds)
        return int(cur.fetchone()[0])


def ledger_attention_count(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*)::int FROM game_ingest WHERE {FLAG_CONDITIONS['attention'][0]}")
        return int(cur.fetchone()[0])


def ledger_row_count(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(LEDGER_COUNT_SQL)
        return int(cur.fetchone()[0])


def ledger_search(
    conn,
    query: str,
    *,
    limit: int,
    offset: int = 0,
    log: bool = True,
    sort: str | None = None,
    sort_dir: str | None = None,
    status: str = "",
    flag: str = "",
) -> list[dict[str, Any]]:
    cond, binds = ledger_where(query, status=status, flag=flag)
    order = ledger_order_sql(sort or "updated_at", sort_dir or "desc")
    sql = SEARCH_COLUMNS.strip() + f"\nWHERE {cond}\n{order}\nLIMIT %s OFFSET %s\n"
    params = binds + (limit, offset)
    if log:
        print(
            "Query: search\n",
            sql.strip(),
            f"\n-- bind: limit = {limit}, offset = {offset}",
            sep="",
        )
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [_row_dict(cur, row) for row in cur.fetchall()]


def ledger_attention(
    conn,
    *,
    limit: int,
    sort: str | None = None,
    sort_dir: str | None = None,
    log: bool = True,
) -> list[dict[str, Any]]:
    order = ledger_order_sql(sort or "updated_at", sort_dir or "desc")
    sql = ATTENTION_SELECT.strip() + f"\n{order}\nLIMIT %s\n"
    if log:
        print("Query: attention rows\n", sql.strip(), f"\n-- bind: limit = {limit}", sep="")
    with conn.cursor() as cur:
        cur.execute(sql, (limit,))
        return [_row_dict(cur, row) for row in cur.fetchall()]


def run_report(
    *,
    search: str = "",
    limit: int = 50,
    include_attention: bool = True,
) -> dict[str, Any]:
    with pg_connect() as conn:
        report: dict[str, Any] = {"summary": ledger_summary(conn)}
        q = search.strip()
        if q:
            report["search"] = {"query": q, "rows": ledger_search(conn, q, limit=limit)}
        elif include_attention:
            report["attention"] = ledger_attention(conn, limit=limit)
    return report


def main(search: str = "", limit: int = 50) -> None:
    """Windmill entrypoint (parameters show in UI)."""
    report = run_report(search=search, limit=limit)
    print("\n--- result (YAML) ---")
    print(yaml.safe_dump(report, sort_keys=False, allow_unicode=True))


def cli_main() -> None:
    ap = argparse.ArgumentParser(description="Ledger health summary and optional search.")
    ap.add_argument("--search", default="", help="Words that must all appear in the row (any field, any order)")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--no-attention", action="store_true")
    ap.add_argument("--format", choices=("yaml", "json"), default="yaml")
    args = ap.parse_args()
    report = run_report(
        search=args.search,
        limit=args.limit,
        include_attention=not args.no_attention,
    )
    if args.format == "json":
        print(json.dumps(report, indent=2))
    else:
        print(yaml.safe_dump(report, sort_keys=False, allow_unicode=True))


if __name__ == "__main__":
    cli_main()
