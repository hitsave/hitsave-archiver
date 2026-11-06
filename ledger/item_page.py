"""Item page: one ledger package, its catalog record and the actions that change it."""
from __future__ import annotations

import html
import json
from datetime import datetime
from typing import Any, Callable
from urllib.parse import urlencode

from formatting import fmt_ts, item_href
from site_ui import render_page_shell

ConnectFn = Callable[[], Any]
JSON_MAX_CHARS = 20_000


def fetch_item(connect: ConnectFn, game_key: str) -> dict[str, Any] | None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM game_ingest WHERE game_key = %s", (game_key,))
            row = cur.fetchone()
            if row is None:
                return None
            cols = [d[0] for d in cur.description]
    return dict(zip(cols, row))


def row_links(game_key: str, item_id, *, admin_base: str, site_base: str) -> tuple[str, str]:
    """(game key cell linking to the item page, actions cell with Omeka admin/site links only)."""
    key_html = f'<a href="{html.escape(item_href(game_key))}">{html.escape(game_key)}</a>'
    if not item_id:
        return key_html, ""
    admin = html.escape(f"{admin_base}/item/{item_id}/show")
    public = html.escape(f"{site_base}/item/{item_id}")
    return key_html, (
        f'<a href="{admin}" target="_blank" rel="noopener">admin ↗</a> '
        f'<a href="{public}" target="_blank" rel="noopener">site ↗</a>'
    )


def _status_badge(status: str) -> str:
    cls = {
        "complete": "badge-ok",
        "failed": "badge-fail",
        "quarantine": "badge-fail",
    }.get(status, "badge-neutral")
    return f'<span class="badge {cls}">{html.escape(status or "(no status)")}</span>'


def _visibility_form(item_id: int, game_key: str, *, make_public: bool) -> str:
    verb = "Publish" if make_public else "Hide"
    question = (
        f"Publish item {item_id} on the public site?"
        if make_public
        else f"Hide item {item_id} from the public site?"
    )
    return (
        '<form method="post" action="/visibility" class="inline-form" '
        f'onsubmit="return confirm({html.escape(json.dumps(question))})">'
        f'<input type="hidden" name="omeka_ref" value="{item_id}">'
        f'<input type="hidden" name="is_public" value="{"true" if make_public else "false"}">'
        f'<input type="hidden" name="return_to" value="{html.escape(item_href(game_key))}">'
        f'<button type="submit" data-busy="{"Publishing…" if make_public else "Hiding…"}">{verb}</button>'
        "</form>"
    )


def _catalog_panel(row: dict[str, Any], *, admin_base: str, site_base: str, is_public: bool | None) -> str:
    item_id = row.get("omeka_item_id")
    game_key = str(row.get("game_key") or "")
    status = str(row.get("status") or "")
    if not item_id:
        return (
            '<div class="panel"><h2>Catalog record</h2>'
            '<p class="muted">Not in the catalog yet; catalog actions appear once the package has an '
            "Omeka record.</p></div>"
        )
    item_id = int(item_id)
    if is_public is True:
        state = '<span class="badge badge-ok">Public</span>'
        forms = _visibility_form(item_id, game_key, make_public=False)
    elif is_public is False:
        state = '<span class="badge badge-neutral">Private</span>'
        forms = _visibility_form(item_id, game_key, make_public=True)
    else:
        state = '<span class="badge badge-warn">Unknown</span> <span class="muted">couldn\'t reach Omeka</span>'
        forms = _visibility_form(item_id, game_key, make_public=True) + _visibility_form(
            item_id, game_key, make_public=False
        )
    ref = urlencode({"omeka_ref": str(item_id)})
    admin = html.escape(f"{admin_base}/item/{item_id}/show")
    public = html.escape(f"{site_base}/item/{item_id}")
    if status == "quarantine":
        # Publishing would undo the quarantine behind the ledger's back; releasing is an admin job.
        forms = ""
        quarantine = (
            f'<p>Quarantined: <code>{html.escape(str(row.get("block_reason") or "no reason recorded"))}</code>. '
            "To release it, ask an admin.</p>"
        )
    else:
        quarantine = f'<a class="link-danger" href="/quarantine?{ref}">Quarantine…</a>'
    return f"""<div class="panel"><h2>Catalog record</h2>
<p>Omeka item <strong>{item_id}</strong> · {state}</p>
<p class="actions-row"><a href="{admin}" target="_blank" rel="noopener">Omeka admin ↗</a>
<a href="{public}" target="_blank" rel="noopener">Public page ↗</a>
<a href="/correct?{ref}">Correct metadata</a></p>
{f'<div class="actions-row">{forms}</div>' if forms else ""}
<div class="actions-row">{quarantine}</div>
</div>"""


def _problems_panel(row: dict[str, Any]) -> str:
    err = row.get("error_message")
    reason = row.get("block_reason")
    if not err and not reason:
        return ""
    parts = ['<div class="panel panel-danger"><h2>Problems</h2>']
    if reason:
        parts.append(f"<p>Block reason: <code>{html.escape(str(reason))}</code></p>")
    if err:
        parts.append(f'<pre class="err">{html.escape(str(err))}</pre>')
    parts.append("</div>")
    return "".join(parts)


def _field_value(value: Any) -> str:
    if value is None:
        return '<span class="muted">—</span>'
    if isinstance(value, datetime):
        return html.escape(fmt_ts(value))
    if isinstance(value, (dict, list)):
        # inventory_json can list thousands of files: fold JSON away and cap what's sent.
        text = json.dumps(value, indent=2, default=str, ensure_ascii=False)
        size = f"{len(value)} {'keys' if isinstance(value, dict) else 'entries'}"
        if len(text) > JSON_MAX_CHARS:
            text = text[:JSON_MAX_CHARS] + f"\n… cut at {JSON_MAX_CHARS:,} characters"
        return f"<details><summary>{size}</summary><pre>{html.escape(text)}</pre></details>"
    return html.escape(str(value))


def _fields_table(row: dict[str, Any]) -> str:
    trs = "".join(
        f"<tr><th scope=\"row\"><code>{html.escape(str(k))}</code></th><td>{_field_value(v)}</td></tr>"
        for k, v in row.items()
    )
    return f'<h2>All fields</h2><div class="table-wrap"><table class="kv"><tbody>{trs}</tbody></table></div>'


def render_item_page(
    row: dict[str, Any],
    *,
    admin_base: str,
    site_base: str,
    is_public: bool | None,
    status_config: dict,
) -> bytes:
    game_key = str(row.get("game_key") or "")
    title = str(row.get("moby_title") or game_key)
    inner = f"""<p><code>{html.escape(game_key)}</code> · {_status_badge(str(row.get("status") or ""))}
 · updated {html.escape(fmt_ts(row.get("updated_at")))}</p>
{_catalog_panel(row, admin_base=admin_base, site_base=site_base, is_public=is_public)}
{_problems_panel(row)}
{_fields_table(row)}
<p><a href="/">← Back to the ledger</a></p>"""
    return render_page_shell(title=title, active="ledger", inner=inner, status_config=status_config)


def render_item_not_found(game_key: str, *, status_config: dict) -> bytes:
    inner = (
        f"<p>No package in the ledger has the game key <code>{html.escape(game_key)}</code>.</p>"
        f'<p><a href="/?{html.escape(urlencode({"q": game_key}))}">Search the ledger for it</a> · '
        '<a href="/">Back to the ledger</a></p>'
    )
    return render_page_shell(title="Package not found", active="ledger", inner=inner, status_config=status_config)
