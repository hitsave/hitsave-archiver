"""Sortable table header links (query-param sort + asc/desc)."""
from __future__ import annotations

import html
from urllib.parse import urlencode


def parse_sort_params(
    sort: str | None,
    direction: str | None,
    *,
    default_sort: str = "updated_at",
    default_dir: str = "desc",
) -> tuple[str, str]:
    s = (sort or "").strip() or default_sort
    d = (direction or "").strip().lower()
    if d not in ("asc", "desc"):
        d = default_dir
    return s, d


def _next_dir(*, col: str, active: bool, current_dir: str) -> str:
    if active:
        return "asc" if current_dir == "desc" else "desc"
    return "desc" if col == "updated_at" else "asc"


def sort_header_link(
    *,
    label: str,
    col: str,
    base_path: str,
    current_sort: str,
    current_dir: str,
    extra: dict[str, str] | None = None,
    th_class: str = "",
) -> str:
    active = current_sort == col
    next_dir = _next_dir(col=col, active=active, current_dir=current_dir)
    params: dict[str, str] = {**(extra or {}), "sort": col, "dir": next_dir}
    params = {k: v for k, v in params.items() if v}
    href = f"{base_path}?{urlencode(params)}"
    if active:
        arrow = "↑" if current_dir == "asc" else "↓"
        aria = f'sortable sorted {current_dir}ending'
    else:
        arrow = "↕"
        aria = "sortable"
    cls = "sortable"
    if active:
        cls += " active"
    if th_class:
        cls += f" {th_class}"
    return (
        f'<th class="{cls}">'
        f'<a href="{html.escape(href)}" aria-sort="{html.escape(aria)}">'
        f"{html.escape(label)} <span class=\"sort-ind\" aria-hidden=\"true\">{arrow}</span>"
        f"</a></th>"
    )
