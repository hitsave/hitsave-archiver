"""Ledger page filters (?status=, ?flag=) and the chips that link to them."""
from __future__ import annotations

import html
from urllib.parse import urlencode

from ledger_health_report import FLAG_CONDITIONS


def parse_filters(qs: dict[str, list[str]]) -> dict[str, str]:
    out: dict[str, str] = {}
    status = (qs.get("status") or [""])[0].strip()
    if status:
        out["status"] = status
    flag = (qs.get("flag") or [""])[0].strip()
    if flag in FLAG_CONDITIONS:
        out["flag"] = flag
    return out


def _chip(href: str, n: int, label: str, cls: str) -> str:
    return f'<a class="{cls}" href="{html.escape(href)}"><strong>{n}</strong>{html.escape(label)}</a>'


def status_chip(status: str, n: int, *, active: bool) -> str:
    cls = "chip chip-active" if active else "chip"
    return _chip("/?" + urlencode({"status": status}), n, status or "(no status)", cls)


def flag_chip(flag: str, n: int, *, active: bool = False) -> str:
    cls = "chip chip-warn chip-active" if active else "chip chip-warn"
    return _chip("/?" + urlencode({"flag": flag}), n, FLAG_CONDITIONS[flag][1], cls)


def filter_banner(filters: dict[str, str]) -> str:
    if not filters:
        return ""
    parts = []
    if filters.get("status"):
        parts.append(f"status <strong>{html.escape(filters['status'])}</strong>")
    if filters.get("flag") in FLAG_CONDITIONS:
        parts.append(f"<strong>{html.escape(FLAG_CONDITIONS[filters['flag']][1])}</strong>")
    return (
        f'<p class="filter-banner">Filtered to {" and ".join(parts)} · '
        '<a href="/">Clear filter</a></p>'
    )
