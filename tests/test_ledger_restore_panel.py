"""Ledger: report a problem, start and approve Glacier restores (ledger/restore_panel.py)."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ledger"))
sys.path.insert(0, str(ROOT / "scripts"))

from restore_panel import (  # noqa: E402
    handle_restore_post,
    render_restore_panel,
    render_restores_page,
)

NOV14 = datetime(2026, 11, 14, 6, 0, tzinfo=timezone.utc)
ADMIN = "admin@example.org"


class FakeStore:
    def __init__(self, has_copy=True):
        self.games = {"pkg": {"id": 1, "game_key": "pkg", "total_bytes": 4 * 10**9, "has_copy": has_copy}}
        self.reports: list[dict] = []
        self.requests: list[dict] = []

    def game(self, game_key):
        return self.games.get(game_key)

    def add_report(self, game_ingest_id, note, user):
        self.reports.append({"id": len(self.reports) + 1, "game_ingest_id": game_ingest_id, "note": note,
                             "reported_by": user, "reported_at": NOV14, "closed_at": None})
        return self.reports[-1]["id"]

    def open_request(self, game_ingest_id):
        return next((r for r in self.requests if r["game_ingest_id"] == game_ingest_id
                     and r["state"] in ("checking", "restoring", "awaiting_approval", "approved", "writing")), None)

    def add_request(self, game_ingest_id, tier, reason, user, report_id):
        if self.open_request(game_ingest_id):
            return None
        self.requests.append({"id": len(self.requests) + 1, "game_ingest_id": game_ingest_id, "state": "checking",
                              "tier": tier, "reason": reason, "requested_by": user, "problem_report_id": report_id})
        return self.requests[-1]["id"]

    def _move(self, request_id, allowed, **fields):
        r = next((r for r in self.requests if r["id"] == request_id), None)
        if r is None or r["state"] not in allowed:
            return False
        r.update(fields)
        return True

    def approve(self, request_id, user):
        return self._move(request_id, ("awaiting_approval",), state="approved", approved_by=user)

    def cancel(self, request_id, user):
        return self._move(request_id, ("checking", "restoring", "awaiting_approval"), state="cancelled")

    def close_report(self, report_id, user, note):
        r = next((r for r in self.reports if r["id"] == report_id and r["closed_at"] is None), None)
        if r is None:
            return False
        r.update(closed_at=NOV14, closed_by=user, close_note=note)
        return True


def post(store, path, user="a@example.org", admins=frozenset({ADMIN}), **form):
    return handle_restore_post(path, {k: [v] for k, v in form.items()}, user, store=store, admins=set(admins))


def state(**over):
    s = {"reports": [], "request": None, "last_wasabi_check": None}
    s.update(over)
    return s


def test_report_saves_note_and_user():
    store = FakeStore()
    assert post(store, "/restore/report", game_key="pkg", note="disc.iso won't open") == (
        "pkg", "ok", "Thanks. An admin will look at this package.")
    assert store.reports[-1]["reported_by"] == "a@example.org"
    assert store.reports[-1]["note"] == "disc.iso won't open"


def test_report_needs_a_note_and_a_user():
    store = FakeStore()
    assert post(store, "/restore/report", game_key="pkg", note="  ")[1] == "fail"
    assert post(store, "/restore/report", user="", game_key="pkg", note="broken")[1] == "fail"
    assert post(store, "/restore/report", game_key="pkg", note="x" * 2001)[1] == "fail"
    assert post(store, "/restore/report", game_key="nope", note="broken")[1] == "fail"
    assert store.reports == []


def test_panel_lists_open_reports():
    report = {"id": 3, "note": "disc.iso won't open", "reported_by": "a@example.org", "reported_at": NOV14}
    html = render_restore_panel(state(reports=[report]), game_key="pkg", is_admin=False, has_copy=True,
                                total_bytes=10**9)
    assert "disc.iso won&#x27;t open" in html and "a@example.org" in html and "2026-11-14" in html
    assert 'action="/restore/report"' in html
    assert 'action="/restore/request"' not in html


def test_restores_page_lists_open_reports_and_requests():
    page = render_restores_page(
        requests=[{"id": 1, "game_key": "pkg", "state": "awaiting_approval", "tier": "Bulk",
                   "requested_by": ADMIN, "requested_at": NOV14, "message": "Restoring 1 corrupted file."}],
        reports=[{"id": 2, "game_key": "other", "note": "missing scans", "reported_by": "a@example.org",
                  "reported_at": NOV14}],
        status_config={},
    ).decode()
    assert "Waiting for approval" in page and "missing scans" in page
    assert "/item?key=pkg" in page and "/item?key=other" in page
