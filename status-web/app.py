#!/usr/bin/env python3
"""Preservation ledger dashboard, health, Windmill ops, catalog correction."""
from __future__ import annotations

import html
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import psycopg
import yaml

from catalog_correct import (
    ledger_hint_for_omeka,
    render_correct_page,
    run_correction,
    validate_and_job_args,
)
from catalog_quarantine import (
    render_quarantine_page,
    run_quarantine,
    validate_and_argv as validate_quarantine_argv,
)
from catalog_visibility import run_visibility
from ledger_health import fetch_attention_rows, fetch_health_summary

sys.path.insert(0, str(Path("/app/scripts")))
from ledger_health_report import (  # noqa: E402
    ledger_order_sql,
    ledger_row_count,
    ledger_search,
    ledger_search_count,
)
from site_ui import render_page_shell
from pagination import (
    ledger_pagination_config,
    parse_page,
    parse_per_page,
    render_pagination_bar,
    total_pages,
)
from table_sort import parse_sort_params, sort_header_link
from windmill_client import (
    fetch_completed_jobs,
    windmill_api_settings,
    windmill_job_ui_url,
    windmill_quick_links,
    windmill_script_ui_url,
)

CONFIG = yaml.safe_load(Path("/config/status-web.yaml").read_text())
DB = yaml.safe_load(Path(CONFIG["database"]["config_file"]).read_text())["postgres"]
_server = CONFIG.get("server") or {}
HOST = str(_server.get("host") or "0.0.0.0").strip() or "0.0.0.0"
PORT = int(_server.get("port") or 8090)
_LOGS = CONFIG.get("logs") or {}
GENERATED_DIR = Path((_LOGS.get("generated_dir") or "/archiver/.generated").strip())


def _omeka_link_bases(cfg: dict) -> tuple[str, str]:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from config_require import require_dict, require_str

    omeka = require_dict(cfg, "omeka", where="status-web.yaml")
    if omeka.get("public_admin_base") and omeka.get("public_site_base"):
        return (
            require_str(omeka, "public_admin_base", where="status-web.yaml omeka").rstrip("/"),
            require_str(omeka, "public_site_base", where="status-web.yaml omeka").rstrip("/"),
        )
    base = require_str(omeka, "public_url", where="status-web.yaml omeka").rstrip("/")
    slug = require_str(omeka, "site_slug", where="status-web.yaml omeka").strip("/")
    return f"{base}/admin", f"{base}/s/{slug}"


ADMIN, SITE = _omeka_link_bases(CONFIG)


def connect():
    return psycopg.connect(
        host=DB["host"],
        port=int(DB.get("port", 5432)),
        dbname=DB["database"],
        user=DB["user"],
        password=DB["password"],
    )


def fetch_summary() -> list[tuple[str, int]]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT status, COUNT(*)::int
                FROM game_ingest
                GROUP BY status
                ORDER BY status
                """
            )
            return list(cur.fetchall())


def fetch_ledger_page(
    q: str,
    *,
    page: int,
    per_page: int,
    sort: str,
    sort_dir: str,
) -> tuple[list[dict], int, int]:
    """Return (rows, total_matching, effective_page)."""
    order = ledger_order_sql(sort, sort_dir)
    with connect() as conn:
        if q.strip():
            total = ledger_search_count(conn, q)
        else:
            total = ledger_row_count(conn)
        pages = total_pages(total, per_page)
        eff_page = parse_page(str(page), total_pages=pages)
        offset = (eff_page - 1) * per_page
        if q.strip():
            rows = ledger_search(
                conn,
                q,
                limit=per_page,
                offset=offset,
                log=False,
                sort=sort,
                sort_dir=sort_dir,
            )
        else:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT game_key, status, moby_status, block_reason, omeka_item_id,
                           omeka_media_id, moby_title, source_path, aip_wasabi_prefix,
                           aip_uploaded_at, updated_at, error_message
                    FROM game_ingest
                    {order}
                    LIMIT %s OFFSET %s
                    """,
                    (per_page, offset),
                )
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    return rows, total, eff_page


def _action_links(item_id) -> list[str]:
    links: list[str] = []
    if not item_id:
        return links
    admin = f"{ADMIN}/item/{item_id}/show"
    public = f"{SITE}/item/{item_id}"
    correct = f"/correct?{urlencode({'omeka_ref': str(item_id)})}"
    quarantine = f"/quarantine?{urlencode({'omeka_ref': str(item_id)})}"
    links.append(f'<a href="{html.escape(admin)}">admin</a>')
    links.append(f'<a href="{html.escape(public)}">site</a>')
    links.append(f'<a href="{html.escape(correct)}">correct</a>')
    links.append(f'<a href="{html.escape(quarantine)}">quarantine</a>')
    ref = html.escape(str(item_id))
    links.append(
        f'<form method="post" action="/visibility" class="inline-form">'
        f'<input type="hidden" name="omeka_ref" value="{ref}">'
        f'<input type="hidden" name="is_public" value="true">'
        f'<button type="submit" class="linkish">publish</button></form>'
    )
    links.append(
        f'<form method="post" action="/visibility" class="inline-form">'
        f'<input type="hidden" name="omeka_ref" value="{ref}">'
        f'<input type="hidden" name="is_public" value="false">'
        f'<button type="submit" class="linkish">hide</button></form>'
    )
    return links


def _render_attention_table(
    rows: list[dict],
    *,
    sort: str,
    sort_dir: str,
    base_path: str = "/health",
) -> str:
    extra: dict[str, str] = {}
    headers = "".join(
        [
            sort_header_link(
                label="Game key",
                col="game_key",
                base_path=base_path,
                current_sort=sort,
                current_dir=sort_dir,
                extra=extra,
            ),
            sort_header_link(
                label="Status",
                col="status",
                base_path=base_path,
                current_sort=sort,
                current_dir=sort_dir,
                extra=extra,
            ),
            sort_header_link(
                label="Error",
                col="error_message",
                base_path=base_path,
                current_sort=sort,
                current_dir=sort_dir,
                extra=extra,
                th_class="col-error",
            ),
            sort_header_link(
                label="Block reason",
                col="block_reason",
                base_path=base_path,
                current_sort=sort,
                current_dir=sort_dir,
                extra=extra,
            ),
            "<th>Actions</th>",
            sort_header_link(
                label="Updated",
                col="updated_at",
                base_path=base_path,
                current_sort=sort,
                current_dir=sort_dir,
                extra=extra,
            ),
        ]
    )
    trs = []
    for row in rows:
        item_id = row.get("omeka_item_id")
        err = row.get("error_message")
        trs.append(
            "<tr>"
            f"<td>{html.escape(str(row.get('game_key') or ''))}</td>"
            f"<td>{html.escape(str(row.get('status') or ''))}</td>"
            f'<td class="col-error err">{html.escape(str(err or ""))}</td>'
            f"<td>{html.escape(str(row.get('block_reason') or ''))}</td>"
            f"<td>{' '.join(_action_links(item_id))}</td>"
            f"<td>{html.escape(str(row.get('updated_at') or ''))}</td>"
            "</tr>"
        )
    return f"""
<div class="table-wrap"><table><thead><tr>
{headers}
</tr></thead><tbody>
{''.join(trs) if trs else '<tr><td colspan="6">Nothing needs attention</td></tr>'}
</tbody></table></div>"""


def list_generated_logs() -> list[Path]:
    if not GENERATED_DIR.is_dir():
        return []
    logs = [p for p in GENERATED_DIR.iterdir() if p.is_file() and p.suffix == ".log"]
    logs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return logs[:30]


def _windmill_scripts_cfg() -> dict[str, str]:
    ops = CONFIG.get("ops") or {}
    wm = ops.get("windmill") or {}
    scripts = wm.get("scripts") if isinstance(wm.get("scripts"), dict) else {}
    if not scripts:
        scripts = ops.get("windmill_scripts") or {}
    defaults = {
        "Remote intake sweep": "f/hitsave/remote_intake_sweep",
        "Wasabi inbox sweep": "f/hitsave/wasabi_redump_inbox_sweep",
        "GDrive inbox sweep": "f/hitsave/gdrive_redump_inbox_sweep",
        "Batch resume Omeka": "f/hitsave/batch_resume_omeka",
        "Ledger health report": "f/hitsave/ledger_health_report",
        "Catalog correction (Windmill)": "f/hitsave/correct_catalog_item",
        "Quarantine catalog item": "f/hitsave/quarantine_catalog_item",
    }
    merged = {**defaults, **{str(k): str(v) for k, v in scripts.items() if v}}
    return merged


def render_ledger(
    q: str = "",
    *,
    sort: str = "updated_at",
    sort_dir: str = "desc",
    page: int = 1,
    per_page: int | None = None,
) -> bytes:
    pag_cfg = ledger_pagination_config(CONFIG)
    pp = per_page if per_page is not None else int(pag_cfg["default_per_page"])
    pp = parse_per_page(str(pp), pag_cfg)
    rows, total, page = fetch_ledger_page(
        q, page=page, per_page=pp, sort=sort, sort_dir=sort_dir
    )
    summary = fetch_summary()
    chips = "".join(
        f'<div class="chip"><strong>{n}</strong>{html.escape(st or "(null)")}</div>'
        for st, n in summary
    )
    nav_extra: dict[str, str] = {"sort": sort, "dir": sort_dir, "per_page": str(pp)}
    if q.strip():
        nav_extra["q"] = q
    sort_extra = {**nav_extra, "page": "1"}
    ledger_headers = "".join(
        [
            sort_header_link(
                label="Game key",
                col="game_key",
                base_path="/",
                current_sort=sort,
                current_dir=sort_dir,
                extra=sort_extra,
            ),
            sort_header_link(
                label="Status",
                col="status",
                base_path="/",
                current_sort=sort,
                current_dir=sort_dir,
                extra=sort_extra,
            ),
            sort_header_link(
                label="Moby",
                col="moby_status",
                base_path="/",
                current_sort=sort,
                current_dir=sort_dir,
                extra=sort_extra,
            ),
            sort_header_link(
                label="Moby title",
                col="moby_title",
                base_path="/",
                current_sort=sort,
                current_dir=sort_dir,
                extra=sort_extra,
            ),
            "<th>Omeka</th>",
            sort_header_link(
                label="Error",
                col="error_message",
                base_path="/",
                current_sort=sort,
                current_dir=sort_dir,
                extra=sort_extra,
                th_class="col-error",
            ),
            sort_header_link(
                label="Updated",
                col="updated_at",
                base_path="/",
                current_sort=sort,
                current_dir=sort_dir,
                extra=sort_extra,
            ),
        ]
    )
    trs = []
    for row in rows:
        item_id = row.get("omeka_item_id")
        err = row.get("error_message")
        err_cell = (
            f'<span class="err">{html.escape(str(err))}</span>' if err else ""
        )
        trs.append(
            "<tr>"
            f"<td>{html.escape(str(row.get('game_key') or ''))}</td>"
            f"<td>{html.escape(str(row.get('status') or ''))}</td>"
            f"<td>{html.escape(str(row.get('moby_status') or ''))}</td>"
            f"<td>{html.escape(str(row.get('moby_title') or ''))}</td>"
            f"<td>{' '.join(_action_links(item_id))}</td>"
            f'<td class="col-error">{err_cell}</td>'
            f"<td>{html.escape(str(row.get('updated_at') or ''))}</td>"
            "</tr>"
        )
    pp_opts = list(pag_cfg["per_page_options"])  # type: ignore[arg-type]
    pagination_html = render_pagination_bar(
        base_path="/",
        page=page,
        per_page=pp,
        total_rows=total,
        extra=nav_extra,
        per_page_options=pp_opts,
    )
    inner = f"""
<div class="summary">{chips or '<span class="muted">No ledger rows yet.</span>'}</div>
<p class="muted"><a href="/health">Ledger health</a> — flags and attention queue.</p>
<form class="search-form" method="get"><input type="search" name="q" value="{html.escape(q)}"
placeholder="Search key, path, status, moby, error, Wasabi prefix, Omeka id, …">
<input type="hidden" name="sort" value="{html.escape(sort)}">
<input type="hidden" name="dir" value="{html.escape(sort_dir)}">
<input type="hidden" name="page" value="1">
<input type="hidden" name="per_page" value="{pp}">
<button type="submit">Search</button></form>
<p class="muted">Matches any of: game key, source path, status, moby status/title/id, block reason,
error text, ingest kind, Wasabi prefixes, dump class/system/category, Omeka item/media id.</p>
{pagination_html}
<div class="table-wrap"><table><thead><tr>
{ledger_headers}
</tr></thead><tbody>
{''.join(trs) if trs else '<tr><td colspan="7">No rows</td></tr>'}
</tbody></table></div>
{pagination_html}"""
    return render_page_shell(
        title="HitSave preservation ledger", active="ledger", inner=inner, status_config=CONFIG
    )


def render_health(*, sort: str = "updated_at", sort_dir: str = "desc") -> bytes:
    summary = fetch_health_summary(connect)
    by_status = summary.get("by_status") or {}
    failed_n = summary.get("failed_or_error_message") or 0
    missing_omeka = summary.get("complete_missing_omeka_item_id") or 0
    status_chips = "".join(
        f'<div class="chip"><strong>{n}</strong>{html.escape(str(st))}</div>'
        for st, n in sorted(by_status.items(), key=lambda x: str(x[0]))
    )
    flag_chips = (
        f'<div class="chip chip-warn"><strong>{failed_n}</strong>failed or error_message</div>'
        f'<div class="chip chip-warn"><strong>{missing_omeka}</strong>complete, no Omeka id</div>'
    )
    rows = fetch_attention_rows(connect, sort=sort, sort_dir=sort_dir)
    inner = f"""
<p>Same flags as Windmill <code>ledger_health_flags</code> / <code>ledger_health_attention</code>.</p>
<h2>By status</h2>
<div class="summary">{status_chips or '<span class="muted">Empty ledger.</span>'}</div>
<h2>Flags</h2>
<div class="summary">{flag_chips}</div>
<h2>Attention queue ({len(rows)} rows, max 150)</h2>
<p>Failed, quarantined, <code>complete</code> without <code>omeka_item_id</code>, or non-empty <code>error_message</code>.</p>
{_render_attention_table(rows, sort=sort, sort_dir=sort_dir)}"""
    return render_page_shell(
        title="Ledger health", active="health", inner=inner, status_config=CONFIG
    )


def render_ops() -> bytes:
    settings = windmill_api_settings(CONFIG.get("ops") or {})
    scripts = _windmill_scripts_cfg()
    note_parts: list[str] = []
    jobs_html = ""
    if settings:
        ui = settings["ui_base"]
        links = windmill_quick_links(ui, scripts)
        link_bits = " · ".join(
            f'<a href="{html.escape(href)}" target="_blank" rel="noopener">{html.escape(label)}</a>'
            for label, href in links
        )
        note_parts.append(f'<p class="actions">{link_bits}</p>')
        jobs, err = fetch_completed_jobs(
            api_base=settings["api_base"],
            workspace=settings["workspace"],
            token=settings["token"],
        )
        if err:
            note_parts.append(f'<p class="err">Windmill API: {html.escape(err)}</p>')
        trs = []
        for row in jobs:
            jid = row.get("id") or ""
            path = html.escape(str(row.get("path") or "—"))
            if jid and ui:
                path = (
                    f'<a href="{html.escape(windmill_job_ui_url(ui, str(jid)))}" '
                    f'target="_blank" rel="noopener">{path}</a>'
                )
            ok = row.get("success")
            if ok is True:
                state = '<span class="badge badge-ok">ok</span>'
            elif ok is False:
                state = '<span class="badge badge-fail">failed</span>'
            else:
                state = html.escape("—")
            trs.append(
                "<tr>"
                f"<td>{path}</td>"
                f"<td>{state}</td>"
                f"<td>{html.escape(str(row.get('created_at') or ''))}</td>"
                f"<td><code>{html.escape(str(jid))}</code></td>"
                "</tr>"
            )
        jobs_html = f"""
<div class="table-wrap"><table><thead><tr><th>Script / flow</th><th>Success</th><th>Created</th><th>Job id</th></tr></thead><tbody>
{''.join(trs) if trs else '<tr><td colspan="4">No completed jobs returned.</td></tr>'}
</tbody></table></div>"""
    else:
        note_parts.append(
            '<p class="muted">Configure <code>ops.windmill.workspace</code> and '
            "<code>ops.windmill_ui_url</code> in status-web.yaml for API job history.</p>"
        )
    inner = f"""
<p>Recent completed jobs from Windmill (when API token is mounted). Mutating runs stay in Windmill.</p>
{''.join(note_parts)}
{jobs_html}"""
    return render_page_shell(
        title="Windmill runs", active="ops", inner=inner, status_config=CONFIG
    )


def render_logs() -> bytes:
    logs = list_generated_logs()
    lis = []
    for path in logs:
        mtime = datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
        lis.append(f"<li><code>{html.escape(path.name)}</code> — {html.escape(mtime)}</li>")
    inner = f"""
<p>Log files under <code>{html.escape(str(GENERATED_DIR))}</code> (newest first, max 30).</p>
<ul class="log-list">{''.join(lis) if lis else '<li class="muted">No .log files or directory not mounted.</li>'}</ul>"""
    return render_page_shell(
        title="Batch log files", active="logs", inner=inner, status_config=CONFIG
    )


class Handler(BaseHTTPRequestHandler):
    def _read_form(self) -> dict[str, list[str]]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        return parse_qs(raw, keep_blank_values=True)

    def _send_html(self, data: bytes, *, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path in ("/", "/index.html"):
            qs = parse_qs(parsed.query)
            q = qs.get("q", [""])[0]
            sort, sort_dir = parse_sort_params(
                qs.get("sort", [""])[0],
                qs.get("dir", [""])[0],
            )
            pag_cfg = ledger_pagination_config(CONFIG)
            per_page = parse_per_page(qs.get("per_page", [""])[0], pag_cfg)
            try:
                page = int(qs.get("page", ["1"])[0])
            except ValueError:
                page = 1
            self._send_html(
                render_ledger(
                    q,
                    sort=sort,
                    sort_dir=sort_dir,
                    page=page,
                    per_page=per_page,
                )
            )
            return
        if path == "/health":
            qs = parse_qs(parsed.query)
            sort, sort_dir = parse_sort_params(
                qs.get("sort", [""])[0],
                qs.get("dir", [""])[0],
            )
            self._send_html(render_health(sort=sort, sort_dir=sort_dir))
            return
        if path == "/failures":
            self.send_response(301)
            self.send_header("Location", "/health")
            self.end_headers()
            return
        if path == "/ops":
            self._send_html(render_ops())
            return
        if path == "/runs":
            self.send_response(301)
            self.send_header("Location", "/ops")
            self.end_headers()
            return
        if path == "/logs":
            self._send_html(render_logs())
            return
        if path == "/correct":
            form = parse_qs(parsed.query, keep_blank_values=True)
            omeka_ref = (form.get("omeka_ref") or [""])[0]
            hint = ledger_hint_for_omeka(connect, omeka_ref) if omeka_ref else None
            self._send_html(
                render_correct_page(
                    form=form, hint=hint, is_post=False, status_config=CONFIG
                ),
            )
            return
        if path == "/quarantine":
            form = parse_qs(parsed.query, keep_blank_values=True)
            omeka_ref = (form.get("omeka_ref") or [""])[0]
            hint = ledger_hint_for_omeka(connect, omeka_ref) if omeka_ref else None
            self._send_html(
                render_quarantine_page(
                    form=form, hint=hint, is_post=False, status_config=CONFIG
                ),
            )
            return
        if path == "/visibility":
            self.send_response(405)
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        form = self._read_form()
        if path == "/correct":
            job_args, errors, values = validate_and_job_args(form, is_post=True)
            hint = ledger_hint_for_omeka(connect, values.get("omeka_ref", ""))
            result = None
            if not errors and job_args is not None:
                result = run_correction(CONFIG.get("ops") or {}, job_args)
                if result[0] != 0:
                    errors.append("Windmill correction job failed (see output below).")
            display_form = dict(form)
            if "description" not in display_form:
                display_form["description"] = [values.get("description", "")]
            self._send_html(
                render_correct_page(
                    form=display_form,
                    hint=hint,
                    errors=errors,
                    result=result,
                    is_post=True,
                    status_config=CONFIG,
                ),
            )
            return
        if path == "/quarantine":
            argv, errors, values = validate_quarantine_argv(form, is_post=True)
            hint = ledger_hint_for_omeka(connect, values.get("omeka_ref", ""))
            result = None
            if not errors and argv:
                result = run_quarantine(argv)
                if result[0] != 0:
                    errors.append("Quarantine script exited with an error (see output below).")
            self._send_html(
                render_quarantine_page(
                    form=form,
                    hint=hint,
                    errors=errors,
                    result=result,
                    is_post=True,
                    status_config=CONFIG,
                ),
            )
            return
        if path == "/visibility":
            omeka_ref = (form.get("omeka_ref") or [""])[0].strip()
            is_public_raw = (form.get("is_public") or [""])[0].strip().lower()
            is_public = is_public_raw in ("true", "1", "yes", "on")
            if omeka_ref:
                run_visibility(omeka_ref, is_public=is_public)
            self.send_response(303)
            back = (form.get("return_to") or [""])[0].strip()
            if not back.startswith("/"):
                referer = (self.headers.get("Referer") or "").strip()
                ref_path = urlparse(referer).path if referer else "/"
                back = ref_path if ref_path.startswith("/") else "/"
            self.send_header("Location", back)
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, fmt: str, *args) -> None:
        return


if __name__ == "__main__":
    HTTPServer((HOST, PORT), Handler).serve_forever()
