"""Small display helpers shared by ledger pages."""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlencode


def fmt_ts(value) -> str:
    """'2026-11-06 12:34 UTC' for datetimes (naive ones are taken as UTC), '—' for None."""
    if value is None:
        return "—"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return str(value)


def item_href(game_key: str) -> str:
    """Link to a package's item page (unescaped; html.escape it in attributes)."""
    return "/item?" + urlencode({"key": game_key})
