"""Ledger health flags and attention queue (same SQL as scripts/ledger_health_report.py)."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

_SCRIPTS = Path("/app/scripts")
if _SCRIPTS.is_dir():
    sys.path.insert(0, str(_SCRIPTS))

from ledger_health_report import ATTENTION_SQL, ledger_attention, ledger_summary  # noqa: E402

ConnectFn = Callable[[], Any]

ATTENTION_LIMIT = 150


def fetch_health_summary(connect: ConnectFn) -> dict[str, Any]:
    with connect() as conn:
        return ledger_summary(conn)


def fetch_attention_rows(
    connect: ConnectFn,
    *,
    limit: int = ATTENTION_LIMIT,
    sort: str | None = None,
    sort_dir: str | None = None,
) -> list[dict[str, Any]]:
    with connect() as conn:
        return ledger_attention(conn, limit=limit, sort=sort, sort_dir=sort_dir, log=False)


def attention_sql_for_ops() -> str:
    return ATTENTION_SQL.strip()
