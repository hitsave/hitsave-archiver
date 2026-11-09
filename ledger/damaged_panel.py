"""Damaged files panel for the item page and the review queue."""
from __future__ import annotations

import html
import sys
from pathlib import Path
from typing import Any

_SCRIPTS = Path("/app/scripts")
if _SCRIPTS.is_dir():
    sys.path.insert(0, str(_SCRIPTS))

from damaged_listing import PUBLIC_PAGE_URL, damaged_files  # noqa: E402


def render_damaged_panel(row: dict[str, Any], *, is_public: bool | None) -> str:
    """A panel listing the package's damaged files, or "" when it has none."""
    files = damaged_files(row.get("inventory_json"))
    if not files:
        return ""
    page = f'<a href="{html.escape(PUBLIC_PAGE_URL)}" target="_blank" rel="noopener">Damaged files page ↗</a>'
    if is_public is True:
        listing = f"This record is public, so they're listed on the public {page}."
    else:
        listing = f"They'll be listed on the public {page} once this record is published."
    none = '<span class="muted">—</span>'
    trs = "".join(
        "<tr>"
        f"<td>{html.escape(f['folder']) or none}</td>"
        f"<td><code>{html.escape(f['name'])}</code></td>"
        f"<td>{html.escape(f['problem'])}</td>"
        f'<td><code class="muted">{html.escape(f["detail"])}</code></td>'
        "</tr>"
        for f in files
    )
    return f"""<div class="panel panel-warn"><h2>Damaged files ({len(files)})</h2>
<p>These files are kept as received, so an intact copy can replace them if one turns up. {listing}</p>
<div class="table-wrap"><table><thead><tr><th scope="col">Folder</th><th scope="col">File</th>
<th scope="col">Problem</th><th scope="col">Checker output</th></tr></thead>
<tbody>{trs}</tbody></table></div>
</div>"""
