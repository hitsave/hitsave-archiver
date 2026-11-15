"""Problem reports and restores from the Glacier copy (docs/restore-from-glacier.md).

Anyone signed in can report a problem with a package. Admins (ledger.yaml restore.admins) start a
restore, approve or cancel it, and close reports. The ledger only writes rows to problem_report and
restore_request; scripts/glacier-restore-worker.py in Windmill does the work in AWS and on Wasabi.
"""
from __future__ import annotations

import html
import json
from typing import Any, Callable

from formatting import fmt_ts, item_href
from restore_states import FINISHED, OPEN, TIERS
from site_ui import render_page_shell

ConnectFn = Callable[[], Any]
NOTE_MAX = 2000
WASABI_CHECK_PREFIX = "wasabi:"  # DETAIL_PREFIX in scripts/wasabi-fixity-check.py

STATE_LABELS = {
    "checking": "Checking Wasabi",
    "restoring": "Waiting for AWS to restore the files",
    "awaiting_approval": "Waiting for approval",
    "approved": "Approved, waiting to write to Wasabi",
    "writing": "Writing to Wasabi",
    "not_needed": "Not needed",
    "cannot_restore": "Can't restore from the copy",
    "done": "Done",
    "failed": "Failed",
    "cancelled": "Cancelled",
    "expired": "Expired",
}
VERDICT_LABELS = {"ok": "fine", "newer": "newer on Wasabi", "missing": "missing", "corrupted": "corrupted"}
WRITTEN_LABELS = {"started": "writing", "yes": "written", "skipped": "skipped", "no": "failed", "": "—"}
# Restore price per GB, plus the download price, paid twice: once to check the files, once to write them.
TIER_USD_PER_GB = {"Bulk": 0.0025, "Standard": 0.02}
TIER_HOURS = {"Bulk": 48, "Standard": 12}
DOWNLOAD_USD_PER_GB = 0.09


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _day(value: Any) -> str:
    return fmt_ts(value)[:10]


def _first(form: dict[str, list[str]], name: str) -> str:
    return (form.get(name) or [""])[0].strip()


class Store:
    """The SQL behind the panel. Tests use a fake with the same methods."""

    def __init__(self, connect: ConnectFn):
        self.connect = connect

    def _rows(self, sql: str, params: tuple = ()) -> list[dict]:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            if cur.description is None:
                return []
            names = [d[0] for d in cur.description]
            return [dict(zip(names, r)) for r in cur.fetchall()]

    def game(self, game_key: str) -> dict | None:
        rows = self._rows(
            "SELECT g.id, g.game_key, COALESCE(g.total_bytes, 0) AS total_bytes, "
            "EXISTS (SELECT 1 FROM package_copy pc WHERE pc.game_ingest_id = g.id "
            "AND pc.location = 'aws-deep-archive') AS has_copy FROM game_ingest g WHERE g.game_key = %s",
            (game_key,),
        )
        return rows[0] if rows else None

    def add_report(self, game_ingest_id: int, note: str, user: str) -> int:
        return self._rows(
            "INSERT INTO problem_report (game_ingest_id, note, reported_by) VALUES (%s, %s, %s) RETURNING id",
            (game_ingest_id, note, user),
        )[0]["id"]

    def open_request(self, game_ingest_id: int) -> dict | None:
        rows = self._rows("SELECT * FROM restore_request WHERE game_ingest_id = %s AND state = ANY(%s)",
                          (game_ingest_id, list(OPEN)))
        return rows[0] if rows else None

    def add_request(self, game_ingest_id: int, tier: str, reason: str, user: str, report_id: int | None) -> int | None:
        import psycopg

        try:
            return self._rows(
                "INSERT INTO restore_request (game_ingest_id, tier, reason, requested_by, problem_report_id) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (game_ingest_id, tier, reason, user, report_id),
            )[0]["id"]
        except psycopg.errors.UniqueViolation:
            return None

    def approve(self, request_id: int, user: str) -> bool:
        return bool(self._rows(
            "UPDATE restore_request SET state = 'approved', approved_by = %s, approved_at = now(), "
            "updated_at = now() WHERE id = %s AND state = 'awaiting_approval' RETURNING id",
            (user, request_id),
        ))

    def cancel(self, request_id: int, user: str) -> bool:
        return bool(self._rows(
            "UPDATE restore_request SET state = 'cancelled', message = %s, updated_at = now(), "
            "finished_at = now() WHERE id = %s AND state IN ('checking', 'restoring', 'awaiting_approval') "
            "RETURNING id",
            (f"Cancelled by {user}.", request_id),
        ))

    def close_report(self, report_id: int, user: str, note: str) -> bool:
        return bool(self._rows(
            "UPDATE problem_report SET closed_by = %s, closed_at = now(), close_note = %s "
            "WHERE id = %s AND closed_at IS NULL RETURNING id",
            (user, note, report_id),
        ))

    def panel_state(self, game_ingest_id: int) -> dict:
        reports = self._rows(
            "SELECT id, note, reported_by, reported_at FROM problem_report "
            "WHERE game_ingest_id = %s AND closed_at IS NULL ORDER BY reported_at DESC",
            (game_ingest_id,),
        )
        requests = self._rows(
            "SELECT * FROM restore_request WHERE game_ingest_id = %s ORDER BY id DESC LIMIT 1", (game_ingest_id,))
        checks = self._rows(
            "SELECT outcome, checked_at, detail FROM fixity_run WHERE game_ingest_id = %s AND detail LIKE %s "
            "ORDER BY checked_at DESC LIMIT 1",
            (game_ingest_id, WASABI_CHECK_PREFIX + "%"),
        )
        return {"reports": reports, "request": requests[0] if requests else None,
                "last_wasabi_check": checks[0] if checks else None}

    def overview(self) -> tuple[list[dict], list[dict]]:
        requests = self._rows(
            "(SELECT r.*, g.game_key FROM restore_request r JOIN game_ingest g ON g.id = r.game_ingest_id "
            " WHERE r.state = ANY(%s) ORDER BY r.id) UNION ALL "
            "(SELECT r.*, g.game_key FROM restore_request r JOIN game_ingest g ON g.id = r.game_ingest_id "
            " WHERE r.state = ANY(%s) ORDER BY r.finished_at DESC NULLS LAST LIMIT 20)",
            (list(OPEN), list(FINISHED)),
        )
        reports = self._rows(
            "SELECT p.id, p.note, p.reported_by, p.reported_at, g.game_key FROM problem_report p "
            "JOIN game_ingest g ON g.id = p.game_ingest_id WHERE p.closed_at IS NULL ORDER BY p.reported_at",
        )
        return requests, reports


# --- POST actions ---------------------------------------------------------------------------

def estimate_usd(total_bytes: int, tier: str) -> float:
    """Upper limit for restoring the whole package and writing it back to Wasabi."""
    gb = total_bytes / 1e9
    return gb * TIER_USD_PER_GB[tier] + gb * 2 * DOWNLOAD_USD_PER_GB


def _id(form: dict[str, list[str]], name: str) -> int | None:
    value = _first(form, name)
    return int(value) if value.isdigit() else None


def handle_restore_post(path: str, form: dict[str, list[str]], user: str, *, store, admins: set[str]
                        ) -> tuple[str, str, str]:
    """(game key to go back to, notice kind "ok" or "fail", message)."""
    game_key = _first(form, "game_key")
    if path == "/restore/report":
        return game_key, *_report(store, game_key, _first(form, "note"), user)
    actions = {"/restore/request": _request, "/restore/approve": _approve, "/restore/cancel": _cancel,
               "/restore/close-report": _close_report}
    if path not in actions:
        return game_key, "fail", "Unknown action."
    if not user or user.lower() not in {a.lower() for a in admins}:
        return game_key, "fail", "Only admins can do this."
    return game_key, *actions[path](store, form, user)


def _request(store, form, user) -> tuple[str, str]:
    tier, reason = _first(form, "tier"), _first(form, "reason")
    if tier not in TIERS:
        return "fail", "Pick Bulk or Standard."
    if not reason:
        return "fail", "Say why the package needs a restore."
    if len(reason) > NOTE_MAX:
        return "fail", f"Keep the reason under {NOTE_MAX:,} characters."
    game = store.game(_first(form, "game_key"))
    if game is None:
        return "fail", "No package has that game key."
    if not game.get("has_copy"):
        return "fail", "This package has no copy in Glacier yet."
    if store.open_request(int(game["id"])) or store.add_request(
            int(game["id"]), tier, reason, user, _id(form, "report_id")) is None:
        return "fail", "A restore is already open for this package."
    return "ok", "Restore started. The job checks Wasabi within 15 minutes."


def _approve(store, form, user) -> tuple[str, str]:
    request_id = _id(form, "request_id")
    if request_id is None or not store.approve(request_id, user):
        return "fail", "This restore isn't waiting for approval any more."
    return "ok", "Approved. The job writes the files to Wasabi within 15 minutes."


def _cancel(store, form, user) -> tuple[str, str]:
    request_id = _id(form, "request_id")
    if request_id is None or not store.cancel(request_id, user):
        return "fail", "This restore can't be cancelled now. It's already writing to Wasabi or has finished."
    return "ok", "Restore cancelled."


def _close_report(store, form, user) -> tuple[str, str]:
    report_id = _id(form, "report_id")
    note = _first(form, "note")[:NOTE_MAX]
    if report_id is None or not store.close_report(report_id, user, note):
        return "fail", "This report is already closed."
    return "ok", "Report closed."


def _report(store, game_key: str, note: str, user: str) -> tuple[str, str]:
    if not user:
        return "fail", "Sign in to report a problem."
    if not note:
        return "fail", "Describe the problem before sending it."
    if len(note) > NOTE_MAX:
        return "fail", f"Keep the description under {NOTE_MAX:,} characters."
    game = store.game(game_key)
    if game is None:
        return "fail", "No package has that game key."
    store.add_report(int(game["id"]), note, user)
    return "ok", "Thanks. An admin will look at this package."


# --- Rendering ------------------------------------------------------------------------------

def _hidden(name: str, value: Any) -> str:
    return f'<input type="hidden" name="{name}" value="{_esc(value)}">'


def _reports_html(reports: list[dict], *, game_key: str, is_admin: bool) -> str:
    if not reports:
        return ""
    items = []
    for r in reports:
        close = ""
        if is_admin:
            close = (f'<form method="post" action="/restore/close-report" class="actions">'
                     f'{_hidden("game_key", game_key)}{_hidden("report_id", r["id"])}'
                     f'<input name="note" placeholder="Why it can be closed" maxlength="{NOTE_MAX}">'
                     f'<button type="submit" class="btn-secondary">Close report</button></form>')
        items.append(f"<li><p>{_esc(r.get('note'))}</p><p class=\"muted\">Reported by "
                     f"{_esc(r.get('reported_by'))} on {_esc(_day(r.get('reported_at')))}</p>{close}</li>")
    return f"<h3>Open reports</h3><ul>{''.join(items)}</ul>"


def _report_form(game_key: str) -> str:
    return (f'<form method="post" action="/restore/report">{_hidden("game_key", game_key)}'
            f'<label>Report a problem with this package<br>'
            f'<textarea name="note" rows="3" maxlength="{NOTE_MAX}" required '
            f'placeholder="Which file, and what did you see?"></textarea></label><br>'
            f'<button type="submit" data-busy="Sending…">Report a problem</button></form>')


def _files_word(n: int) -> str:
    return "file" if n == 1 else "files"


def _usd(value: float) -> str:
    return "less than $0.01" if value < 0.005 else f"about ${value:,.2f}"


def _wasabi_hint(check: dict | None) -> str:
    if not check:
        return ""
    day = _day(check.get("checked_at"))
    if check.get("outcome") == "pass":
        text = (f"The last Wasabi check, on {day}, passed, so these files may not need a restore. The restore "
                "job checks every file first and restores only the ones that are missing or corrupted.")
    else:
        text = f"The last Wasabi check, on {day}, failed: {check.get('detail') or ''}"
    return f'<p class="muted">{_esc(text)}</p>'


def _request_form(state: dict, *, game_key: str, total_bytes: int) -> str:
    reports = state.get("reports") or []
    report = _hidden("report_id", reports[0]["id"]) if reports else ""
    estimates = " ".join(
        f"{tier} (up to {TIER_HOURS[tier]} hours): {_usd(estimate_usd(total_bytes, tier))}." for tier in TIERS)
    options = "".join(f'<option value="{t}"{" selected" if t == "Bulk" else ""}>{t}</option>' for t in TIERS)
    return (f'<h3>Restore from Glacier</h3>{_wasabi_hint(state.get("last_wasabi_check"))}'
            f'<form method="post" action="/restore/request">{_hidden("game_key", game_key)}{report}'
            f'<label>Speed <select name="tier">{options}</select></label><br>'
            f'<label>Reason<br><textarea name="reason" rows="2" maxlength="{NOTE_MAX}" required></textarea>'
            f'</label><br><p class="muted">Cost for the whole package: {_esc(estimates)} These are upper '
            f'limits, because only missing and corrupted files are restored.</p>'
            f'<button type="submit" data-busy="Starting…">Restore from Glacier</button></form>')


def _file_table(files: list[dict]) -> str:
    shown = [f for f in files if f.get("verdict") != "ok"]
    fine = len(files) - len(shown)
    rows = "".join(
        f"<tr><td><code>{_esc(f.get('rel'))}</code></td><td>{_esc(VERDICT_LABELS.get(f.get('verdict'), f.get('verdict')))}</td>"
        f"<td>{'yes' if f.get('restorable') else 'no'}</td><td>{_esc(WRITTEN_LABELS.get(f.get('written') or '', f.get('written')))}</td>"
        f"<td>{_esc(f.get('note'))}</td></tr>" for f in shown)
    table = (f'<table class="kv"><thead><tr><th>File</th><th>Label</th><th>Restorable</th><th>Written</th>'
             f'<th>Note</th></tr></thead><tbody>{rows}</tbody></table>') if rows else ""
    fine_line = f'<p class="muted">{fine} {_files_word(fine)} {"is" if fine == 1 else "are"} fine.</p>' if fine else ""
    return table + fine_line


def _open_request_html(req: dict, *, game_key: str, is_admin: bool) -> str:
    files = files_of(req)
    state = str(req.get("state"))
    parts = [f"<h3>Restore: {_esc(STATE_LABELS.get(state, state))}</h3>",
             f"<p class=\"muted\">{_esc(req.get('tier'))}, started by {_esc(req.get('requested_by'))}"
             f" on {_esc(_day(req.get('requested_at')))}. Reason: {_esc(req.get('reason'))}</p>"]
    if req.get("message"):
        parts.append(f"<p>{_esc(req.get('message'))}</p>")
    parts.append(_file_table(files))
    ids = _hidden("game_key", game_key) + _hidden("request_id", req.get("id"))
    if is_admin and state == "awaiting_approval":
        n = sum(1 for f in files if f.get("restorable"))
        parts.append(f'<p>Approving writes {n} {_files_word(n)} to Wasabi.</p>'
                     f'<form method="post" action="/restore/approve" class="actions">{ids}'
                     f'<button type="submit" data-busy="Approving…">Approve</button></form>')
    if is_admin and state in ("checking", "restoring", "awaiting_approval"):
        parts.append(f'<form method="post" action="/restore/cancel" class="actions">{ids}'
                     f'<button type="submit" class="btn-secondary" data-busy="Cancelling…">Cancel</button></form>')
    return "".join(parts)


def render_restore_panel(state: dict, *, game_key: str, is_admin: bool, has_copy: bool, total_bytes: int) -> str:
    parts = [_reports_html(state.get("reports") or [], game_key=game_key, is_admin=is_admin), _report_form(game_key)]
    req = state.get("request")
    if req and req.get("state") in OPEN:
        parts.append(_open_request_html(req, game_key=game_key, is_admin=is_admin))
    else:
        if req:
            label = STATE_LABELS.get(str(req.get("state")), str(req.get("state")))
            when = _day(req.get("finished_at") or req.get("updated_at"))
            parts.append(f"<p class=\"muted\">Last restore: {_esc(label)}, {_esc(when)}. "
                         f"{_esc(req.get('message') or '')}</p>")
        if is_admin and has_copy:
            parts.append(_request_form(state, game_key=game_key, total_bytes=total_bytes))
        elif is_admin:
            parts.append("<p class=\"muted\">This package has no copy in Glacier yet, so it can't be restored.</p>")
    return f'<div class="panel"><h2>Problems and restores</h2>{"".join(parts)}</div>'


def render_restores_page(*, requests: list[dict], reports: list[dict], status_config: dict) -> bytes:
    def pkg(row: dict) -> str:
        key = str(row.get("game_key") or "")
        return f'<a href="{_esc(item_href(key))}"><code>{_esc(key)}</code></a>'

    report_rows = "".join(
        f"<tr><td>{pkg(r)}</td><td>{_esc(r.get('note'))}</td><td>{_esc(r.get('reported_by'))}</td>"
        f"<td>{_esc(_day(r.get('reported_at')))}</td></tr>" for r in reports)
    request_rows = "".join(
        f"<tr><td>{pkg(r)}</td><td>{_esc(STATE_LABELS.get(r.get('state'), r.get('state')))}</td>"
        f"<td>{_esc(r.get('tier'))}</td><td>{_esc(r.get('requested_by'))}</td>"
        f"<td>{_esc(_day(r.get('requested_at')))}</td><td>{_esc(r.get('message'))}</td></tr>" for r in requests)
    inner = f"""<h1>Restores</h1>
<p class="muted">Problems reported with preserved packages, and restores from the Glacier copy.
See docs/restore-from-glacier.md.</p>
<h2>Open reports</h2>
{f'<table class="kv"><thead><tr><th>Package</th><th>Problem</th><th>Reported by</th><th>On</th></tr></thead><tbody>{report_rows}</tbody></table>' if report_rows else '<p>No open reports.</p>'}
<h2>Restore requests</h2>
{f'<table class="kv"><thead><tr><th>Package</th><th>State</th><th>Speed</th><th>Requested by</th><th>On</th><th>Message</th></tr></thead><tbody>{request_rows}</tbody></table>' if request_rows else '<p>No restores yet.</p>'}"""
    return render_page_shell(title="Restores", active="restores", inner=inner, status_config=status_config)


def files_of(request: dict | None) -> list[dict]:
    raw = (request or {}).get("files") or []
    return json.loads(raw) if isinstance(raw, str) else list(raw)


__all__ = ["Store", "handle_restore_post", "render_restore_panel", "render_restores_page", "TIERS"]
