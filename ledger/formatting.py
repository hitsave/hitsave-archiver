"""Small display helpers shared by ledger pages."""
from __future__ import annotations

import html
from datetime import datetime, timezone
from urllib.parse import urlencode

ERROR_PREVIEW_CHARS = 120
ERROR_FULL_MAX = 2000

_STATUS_BADGE = {"complete": "badge-ok", "failed": "badge-fail", "quarantine": "badge-fail"}
_MOBY_BADGE = {"ok": "badge-ok", "ambiguous": "badge-warn", "no_match": "badge-neutral"}


def fmt_ts(value) -> str:
    """'2026-11-06 12:34 UTC' for datetimes (naive ones are taken as UTC), '—' for None."""
    if value is None:
        return "—"
    if isinstance(value, str):
        try:
            text = value[:-1] + "+00:00" if value.endswith("Z") else value
            value = datetime.fromisoformat(text)
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


def status_badge(status: str | None) -> str:
    status = status or ""
    cls = _STATUS_BADGE.get(status, "badge-neutral")
    return f'<span class="badge {cls}">{html.escape(status or "(no status)")}</span>'


def moby_badge(moby_status: str | None) -> str:
    if not moby_status:
        return '<span class="muted">—</span>'
    cls = _MOBY_BADGE.get(moby_status, "badge-neutral")
    return f'<span class="badge {cls}">{html.escape(moby_status)}</span>'


def error_cell(err: str | None) -> str:
    """A table cell's error: one short line as-is, anything longer folded behind its first line."""
    text = str(err or "").strip()
    if not text:
        return ""
    first = text.splitlines()[0]
    if first == text and len(text) <= ERROR_PREVIEW_CHARS:
        return f'<span class="err">{html.escape(text)}</span>'
    preview = first[:ERROR_PREVIEW_CHARS].rstrip() + "…"
    full = text
    if len(full) > ERROR_FULL_MAX:
        full = full[:ERROR_FULL_MAX] + "\n… cut here; see the item page for the full error"
    return (
        f'<details class="err-more"><summary><span class="err">{html.escape(preview)}</span></summary>'
        f'<pre class="err">{html.escape(full)}</pre></details>'
    )


def problem_text(row: dict) -> str:
    """What the table's Error column shows: the error, else the block reason of a quarantined or
    failed package (on complete packages block_reason only notes how the MobyGames match went)."""
    err = str(row.get("error_message") or "").strip()
    if err:
        return err
    if row.get("status") in ("quarantine", "failed"):
        return str(row.get("block_reason") or "").strip()
    return ""
