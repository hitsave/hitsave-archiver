#!/usr/bin/env python3
"""HitSave Ledger: preservation ledger dashboard, health, Windmill ops, catalog correction."""
from __future__ import annotations

import html
import os
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import psycopg
import yaml

from catalog_correct import (
    correct_get,
    correct_post,
    ledger_hint_for_omeka,
    parse_omeka_item_id,
    run_correction,
)
from catalog_quarantine import (
    needs_confirmation as needs_quarantine_confirmation,
    render_quarantine_page,
    run_quarantine,
    validate_and_argv as validate_quarantine_argv,
)
from catalog_visibility import run_visibility
from ledger_health import fetch_attention_rows, fetch_health_summary
from intake_failures import load_failures, render_intake_failures
from item_page import fetch_item, render_item_not_found, render_item_page, row_links
from omeka_status import fetch_item_is_public, fetch_item_json, fetch_private_item_ids

sys.path.insert(0, str(Path("/app/scripts")))
from ledger_health_report import (  # noqa: E402
    ledger_attention_count,
    ledger_search,
    ledger_search_count,
)
from filters import filter_banner, flag_chip, parse_filters, status_chip  # noqa: E402
from formatting import error_cell, fmt_ts, moby_badge, problem_text, status_badge
from notice import render_notice, safe_return_path, visibility_message, with_notice
from review_queue import (
    HiddenItems,
    next_in_queue,
    parse_queue_args,
    queue_counts,
    render_review_empty,
    render_review_page,
    render_review_unavailable,
)
from site_ui import render_page_shell, set_current_notice, set_current_user
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

def _config_path() -> Path:
    """Private operator file (hitsave-archiver-config/ledger.yaml). Old status-web names still work."""
    for env in ("HITSAVE_LEDGER_CONFIG", "HITSAVE_STATUS_WEB_CONFIG"):
        if os.environ.get(env):
            return Path(os.environ[env])
    for cand in ("/run/hitsave/ledger.yaml", "/config/ledger.yaml", "/config/status-web.yaml"):
        if Path(cand).is_file():
            return Path(cand)
    return Path("/run/hitsave/ledger.yaml")


CONFIG = yaml.safe_load(_config_path().read_text())
DB = yaml.safe_load(Path(CONFIG["database"]["config_file"]).read_text())["postgres"]
_server = CONFIG.get("server") or {}
HOST = str(_server.get("host") or "0.0.0.0").strip() or "0.0.0.0"
PORT = int(_server.get("port") or 8090)
_LOGS = CONFIG.get("logs") or {}
GENERATED_DIR = Path((_LOGS.get("generated_dir") or "/archiver/.generated").strip())
# Only behind oauth2-proxy (no published port): trust its X-Forwarded-Email / X-Forwarded-User.
TRUST_FORWARDED_USER = bool(_server.get("trust_forwarded_user"))


def forwarded_user(headers) -> str:
    """Signed-in user as passed by oauth2-proxy, or '' when not trusted / not present."""
    if not TRUST_FORWARDED_USER:
        return ""
    for name in ("X-Forwarded-Email", "X-Forwarded-Preferred-Username", "X-Forwarded-User"):
        val = (headers.get(name) or "").strip()
        if val:
            return val[:200]
    return ""


def audit(action: str, user: str, **fields: str) -> None:
    """One line per state-changing action in the container log."""
    parts = " ".join(f"{k}={v!r}" for k, v in fields.items())
    print(f"{datetime.now().isoformat(timespec='seconds')} action={action} user={user or '-'} {parts}", flush=True)


def _tag_note(form: dict[str, list[str]], field: str, user: str) -> None:
    """Append '(by user)' to a free-text note that ends up in the package / catalog record."""
    if not user:
        return
    val = (form.get(field) or [""])[0].strip()
    if val and not val.endswith(f"(by {user})"):
        form[field] = [f"{val} (by {user})"]


def _omeka_link_bases(cfg: dict) -> tuple[str, str]:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from config_require import require_dict, require_str

    omeka = require_dict(cfg, "omeka", where="ledger.yaml")
    if omeka.get("public_admin_base") and omeka.get("public_site_base"):
        return (
            require_str(omeka, "public_admin_base", where="ledger.yaml omeka").rstrip("/"),
            require_str(omeka, "public_site_base", where="ledger.yaml omeka").rstrip("/"),
        )
    base = require_str(omeka, "public_url", where="ledger.yaml omeka").rstrip("/")
    slug = require_str(omeka, "site_slug", where="ledger.yaml omeka").strip("/")
    return f"{base}/admin", f"{base}/s/{slug}"


ADMIN, SITE = _omeka_link_bases(CONFIG)


HIDDEN = HiddenItems(
    fetch_private_item_ids,
    spawn=lambda fn: threading.Thread(target=fn, daemon=True).start(),
)


def render_review(qs: dict[str, list[str]]) -> bytes | str:
    """The review queue page, or a redirect path when the shown record turned out to be public."""
    hidden = HIDDEN.ids()
    if hidden is None:
        return render_review_unavailable(status_config=CONFIG)
    moby, after = parse_queue_args(qs)
    counts = queue_counts(connect, hidden)
    row = next_in_queue(connect, hidden, moby=moby, after=after)
    if row is None:
        return render_review_empty(counts=counts, moby=moby, status_config=CONFIG)
    item = fetch_item_json(int(row["omeka_item_id"]))
    if item is not None and item.get("o:is_public") is True:
        # Published elsewhere since the hidden list was read: drop it and move on.
        HIDDEN.mark_public(int(row["omeka_item_id"]))
        params = {k: v for k, v in (("moby", moby), ("after", str(row["id"]))) if v}
        return "/review?" + urlencode(params)
    return render_review_page(
        row=row, item=item, counts=counts, moby=moby, status_config=CONFIG, admin_base=ADMIN
    )


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
    filters: dict[str, str] | None = None,
) -> tuple[list[dict], int, int, int]:
    """Return (rows, total_matching, effective_page, attention_count)."""
    f = filters or {}
    with connect() as conn:
        total = ledger_search_count(conn, q, status=f.get("status", ""), flag=f.get("flag", ""))
        pages = total_pages(total, per_page)
        eff_page = parse_page(str(page), total_pages=pages)
        rows = ledger_search(
            conn,
            q,
            limit=per_page,
            offset=(eff_page - 1) * per_page,
            log=False,
            sort=sort,
            sort_dir=sort_dir,
            status=f.get("status", ""),
            flag=f.get("flag", ""),
        )
        attention = ledger_attention_count(conn)
    return rows, total, eff_page, attention


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
            '<th class="col-links">Omeka</th>',
            sort_header_link(
                label="Updated",
                col="updated_at",
                th_class="col-time",
                base_path=base_path,
                current_sort=sort,
                current_dir=sort_dir,
                extra=extra,
            ),
        ]
    )
    trs = []
    for row in rows:
        key_cell, actions = row_links(
            str(row.get("game_key") or ""), row.get("omeka_item_id"), admin_base=ADMIN, site_base=SITE
        )
        trs.append(
            "<tr>"
            f"<td>{key_cell}</td>"
            f"<td>{status_badge(row.get('status'))}</td>"
            f'<td class="col-error">{error_cell(row.get("error_message"))}</td>'
            f"<td>{html.escape(str(row.get('block_reason') or ''))}</td>"
            f'<td class="col-links">{actions}</td>'
            f'<td class="col-time">{html.escape(fmt_ts(row.get("updated_at")))}</td>'
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
    filters: dict[str, str] | None = None,
) -> bytes:
    filters = filters or {}
    pag_cfg = ledger_pagination_config(CONFIG)
    pp = per_page if per_page is not None else int(pag_cfg["default_per_page"])
    pp = parse_per_page(str(pp), pag_cfg)
    rows, total, page, attention_n = fetch_ledger_page(
        q, page=page, per_page=pp, sort=sort, sort_dir=sort_dir, filters=filters
    )
    summary = fetch_summary()
    chips = "".join(
        status_chip(st or "", n, active=filters.get("status") == st) for st, n in summary
    )
    if summary:
        chips += flag_chip("attention", attention_n, active=filters.get("flag") == "attention")
    nav_extra: dict[str, str] = {"sort": sort, "dir": sort_dir, "per_page": str(pp), **filters}
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
            '<th class="col-links">Omeka</th>',
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
                th_class="col-time",
                base_path="/",
                current_sort=sort,
                current_dir=sort_dir,
                extra=sort_extra,
            ),
        ]
    )
    trs = []
    for row in rows:
        key_cell, actions = row_links(
            str(row.get("game_key") or ""), row.get("omeka_item_id"), admin_base=ADMIN, site_base=SITE
        )
        trs.append(
            "<tr>"
            f"<td>{key_cell}</td>"
            f"<td>{status_badge(row.get('status'))}</td>"
            f"<td>{moby_badge(row.get('moby_status'))}</td>"
            f"<td>{html.escape(str(row.get('moby_title') or ''))}</td>"
            f'<td class="col-links">{actions}</td>'
            f'<td class="col-error">{error_cell(problem_text(row))}</td>'
            f'<td class="col-time">{html.escape(fmt_ts(row.get("updated_at")))}</td>'
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
{filter_banner(filters)}
<form class="search-form" method="get"><input type="search" name="q" value="{html.escape(q)}"
placeholder="Words from the title, e.g. famitsu wave dvd 2008">
{"".join(f'<input type="hidden" name="{k}" value="{html.escape(v)}">' for k, v in filters.items())}
<input type="hidden" name="sort" value="{html.escape(sort)}">
<input type="hidden" name="dir" value="{html.escape(sort_dir)}">
<input type="hidden" name="page" value="1">
<input type="hidden" name="per_page" value="{pp}">
<button type="submit">Search</button></form>
<p class="muted">Every word must appear somewhere in the row, in any order; case, dashes and
punctuation don't matter. Searches game key, source path, status, Moby status/title/id, block reason,
error text, ingest kind, Wasabi prefixes, dump class/system/category and Omeka item/media id.</p>
{pagination_html}
<div class="table-wrap"><table><thead><tr>
{ledger_headers}
</tr></thead><tbody>
{''.join(trs) if trs else '<tr><td colspan="7">No rows</td></tr>'}
</tbody></table></div>
{pagination_html}"""
    return render_page_shell(
        title="Ledger", active="ledger", inner=inner, status_config=CONFIG
    )


def render_health(*, sort: str = "updated_at", sort_dir: str = "desc") -> bytes:
    summary = fetch_health_summary(connect)
    by_status = summary.get("by_status") or {}
    failed_n = summary.get("failed_or_error_message") or 0
    missing_omeka = summary.get("complete_missing_omeka_item_id") or 0
    status_chips = "".join(
        status_chip(str(st), n, active=False)
        for st, n in sorted(by_status.items(), key=lambda x: str(x[0]))
    )
    flag_chips = flag_chip("failed_or_error", failed_n) + flag_chip("no_omeka", missing_omeka)
    rows = fetch_attention_rows(connect, sort=sort, sort_dir=sort_dir)
    with connect() as conn:
        attention_total = ledger_attention_count(conn)
    intake_open, intake_resolved, intake_here = load_failures(connect)
    if intake_here:
        flag_chips += (
            f'<a class="chip chip-warn" href="#intake-failures"><strong>{len(intake_open)}</strong>'
            "intake failures</a>"
        )
    if attention_total > len(rows):
        attention_heading = f"Attention queue (showing {len(rows)} of {attention_total})"
    else:
        attention_heading = f"Attention queue ({attention_total})"
    inner = f"""
<p>Same flags as Windmill <code>ledger_health_flags</code> / <code>ledger_health_attention</code>.</p>
<h2>By status</h2>
<div class="summary">{status_chips or '<span class="muted">Empty ledger.</span>'}</div>
<h2>Flags</h2>
<div class="summary">{flag_chips}</div>
<h2>{attention_heading}</h2>
<p>Failed or quarantined packages, complete packages without a catalog record, and anything with an
error. <a href="/?flag=attention">See all {attention_total} on the Ledger</a>, where you can search
and page through them.</p>
{_render_attention_table(rows, sort=sort, sort_dir=sort_dir)}
<h2 id="intake-failures">Intake failures{f' ({len(intake_open)})' if intake_here else ''}</h2>
{render_intake_failures(intake_open, intake_resolved, available=intake_here)}"""
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
                f"<td>{html.escape(fmt_ts(row.get('started_at') or ''))}</td>"
                f"<td><code>{html.escape(str(jid))}</code></td>"
                "</tr>"
            )
        jobs_html = f"""
<div class="table-wrap"><table><thead><tr><th>Script / flow</th><th>Success</th><th>Started</th><th>Job id</th></tr></thead><tbody>
{''.join(trs) if trs else '<tr><td colspan="4">No completed jobs returned.</td></tr>'}
</tbody></table></div>"""
    else:
        note_parts.append(
            '<p class="muted">Configure <code>ops.windmill.workspace</code> and '
            "<code>ops.windmill_ui_url</code> in ledger.yaml for API job history.</p>"
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
        mtime = fmt_ts(datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc))
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
        set_current_user(forwarded_user(self.headers))
        parsed = urlparse(self.path)
        set_current_notice(render_notice(parse_qs(parsed.query)))
        path = parsed.path.rstrip("/") or "/"
        if path == "/healthz":
            body = b"ok\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
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
                    filters=parse_filters(qs),
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
        if path == "/review":
            page = render_review(parse_qs(parsed.query))
            if isinstance(page, str):
                self.send_response(303)
                self.send_header("Location", page)
                self.end_headers()
            else:
                self._send_html(page)
            return
        if path == "/item":
            game_key = (parse_qs(parsed.query).get("key") or [""])[0]
            row = fetch_item(connect, game_key) if game_key else None
            if row is None:
                self._send_html(render_item_not_found(game_key, status_config=CONFIG), status=404)
                return
            item_id = row.get("omeka_item_id")
            is_public = fetch_item_is_public(int(item_id)) if item_id else None
            self._send_html(
                render_item_page(
                    row, admin_base=ADMIN, site_base=SITE, is_public=is_public, status_config=CONFIG
                )
            )
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
            qs = parse_qs(parsed.query, keep_blank_values=True)
            self._send_html(
                correct_get(
                    qs,
                    fetch_item=fetch_item_json,
                    hint_for=lambda ref: ledger_hint_for_omeka(connect, ref),
                    status_config=CONFIG,
                )
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
        user = forwarded_user(self.headers)
        set_current_user(user)
        set_current_notice("")
        if path == "/correct":
            if (form.get("step") or [""])[0] == "apply":
                _tag_note(form, "correction_summary", user)
            self._send_html(
                correct_post(
                    form,
                    fetch_item=fetch_item_json,
                    hint_for=lambda ref: ledger_hint_for_omeka(connect, ref),
                    run=lambda args: run_correction(CONFIG.get("ops") or {}, args),
                    audit=lambda ref: audit("correct", user, omeka_ref=ref),
                    status_config=CONFIG,
                )
            )
            return
        if path == "/quarantine":
            confirming = needs_quarantine_confirmation(form)
            if not confirming:
                _tag_note(form, "note", user)
            argv, errors, values = validate_quarantine_argv(form, is_post=True)
            hint = ledger_hint_for_omeka(connect, values.get("omeka_ref", ""))
            result = None
            if not errors and argv and confirming:
                self._send_html(
                    render_quarantine_page(
                        form=form, hint=hint, is_post=True, status_config=CONFIG, confirm=True
                    ),
                )
                return
            if not errors and argv:
                audit("quarantine", user, omeka_ref=values.get("omeka_ref", ""))
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
        if path == "/review/publish":
            omeka_ref = (form.get("omeka_ref") or [""])[0].strip()
            moby, after = parse_queue_args(form)
            item_id = parse_omeka_item_id(omeka_ref)
            if item_id is None:
                code, output = 1, f"Invalid omeka_ref: {omeka_ref!r}"
            else:
                code, output = run_visibility(omeka_ref, is_public=True)
                audit("publish", user, omeka_ref=omeka_ref, exit=str(code), via="review")
            if code == 0 and item_id is not None:
                HIDDEN.mark_public(item_id)
                nxt = after
            else:
                print(output, flush=True)
                nxt = max(0, after - 1)  # show the same record again
            kind, msg = visibility_message(omeka_ref, True, code, output)
            params = {k: v for k, v in (("moby", moby), ("after", str(nxt) if nxt else "")) if v}
            self.send_response(303)
            self.send_header("Location", with_notice("/review?" + urlencode(params), kind, msg))
            self.end_headers()
            return
        if path == "/visibility":
            omeka_ref = (form.get("omeka_ref") or [""])[0].strip()
            is_public_raw = (form.get("is_public") or [""])[0].strip().lower()
            is_public = is_public_raw in ("true", "1", "yes", "on")
            if omeka_ref:
                code, output = run_visibility(omeka_ref, is_public=is_public)
                audit("publish" if is_public else "hide", user, omeka_ref=omeka_ref, exit=str(code))
                if code != 0:
                    print(output, flush=True)
                elif (item_id := parse_omeka_item_id(omeka_ref)) is not None:
                    (HIDDEN.mark_public if is_public else HIDDEN.mark_private)(item_id)
                kind, msg = visibility_message(omeka_ref, is_public, code, output)
            else:
                kind, msg = "fail", "No Omeka item given."
            back = safe_return_path(
                (form.get("return_to") or [""])[0], self.headers.get("Referer") or ""
            )
            self.send_response(303)
            self.send_header("Location", with_notice(back, kind, msg))
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, fmt: str, *args) -> None:
        return


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    server.serve_forever()
