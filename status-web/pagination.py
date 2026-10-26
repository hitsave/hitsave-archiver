"""Ledger table pagination (query params + nav HTML)."""
from __future__ import annotations

import html
import math
from urllib.parse import urlencode


def ledger_pagination_config(status_config: dict) -> dict[str, int | list[int]]:
    block = status_config.get("ledger") or {}
    if not isinstance(block, dict):
        block = {}
    default = int(block.get("default_per_page") or 50)
    max_pp = int(block.get("max_per_page") or 200)
    raw_opts = block.get("per_page_options") or [25, 50, 100, 200]
    opts = sorted({int(x) for x in raw_opts if int(x) > 0})
    if not opts:
        opts = [25, 50, 100, 200]
    opts = [o for o in opts if o <= max_pp]
    if default not in opts:
        opts.append(default)
        opts.sort()
    return {"default_per_page": default, "max_per_page": max_pp, "per_page_options": opts}


def parse_page(raw: str | None, *, total_pages: int) -> int:
    try:
        page = int((raw or "1").strip())
    except ValueError:
        page = 1
    if page < 1:
        page = 1
    if total_pages > 0 and page > total_pages:
        page = total_pages
    return page


def parse_per_page(raw: str | None, cfg: dict[str, int | list[int]]) -> int:
    default = int(cfg["default_per_page"])
    max_pp = int(cfg["max_per_page"])
    opts = set(cfg["per_page_options"])  # type: ignore[arg-type]
    try:
        n = int((raw or str(default)).strip())
    except ValueError:
        n = default
    if n in opts:
        return n
    if 1 <= n <= max_pp:
        return n
    return default


def total_pages(total_rows: int, per_page: int) -> int:
    if total_rows <= 0:
        return 1
    return max(1, math.ceil(total_rows / per_page))


def render_pagination_bar(
    *,
    base_path: str,
    page: int,
    per_page: int,
    total_rows: int,
    extra: dict[str, str],
    per_page_options: list[int],
) -> str:
    pages = total_pages(total_rows, per_page)
    page = min(max(1, page), pages)
    start = (page - 1) * per_page + 1 if total_rows else 0
    end = min(page * per_page, total_rows)

    def link(label: str, target_page: int, *, disabled: bool = False) -> str:
        if disabled:
            return f'<span class="page-btn disabled">{html.escape(label)}</span>'
        params = {**extra, "page": str(target_page), "per_page": str(per_page)}
        params = {k: v for k, v in params.items() if v}
        href = f"{base_path}?{urlencode(params)}"
        return f'<a class="page-btn" href="{html.escape(href)}">{html.escape(label)}</a>'

    prev_dis = page <= 1
    next_dis = page >= pages
    nav = (
        f'<div class="pagination">'
        f"{link('← Prev', page - 1, disabled=prev_dis)}"
        f'<span class="page-status">Page <strong>{page}</strong> of <strong>{pages}</strong>'
        f" · showing {start}–{end} of {total_rows}</span>"
        f"{link('Next →', page + 1, disabled=next_dis)}"
        f"</div>"
    )

    opts = []
    for n in per_page_options:
        sel = " selected" if n == per_page else ""
        opts.append(f'<option value="{n}"{sel}>{n}</option>')
    params_hidden = {k: v for k, v in extra.items() if v and k not in ("page", "per_page")}
    hidden = "".join(
        f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">'
        for k, v in params_hidden.items()
    )
    per_form = f"""
<form class="per-page-form" method="get" action="{html.escape(base_path)}">
{hidden}
<input type="hidden" name="page" value="1">
<label class="per-page-label">Rows per page
<select name="per_page" onchange="this.form.submit()">{''.join(opts)}</select></label>
</form>"""
    return f'<div class="pagination-bar">{nav}{per_form}</div>'
