"""Ledger: report a problem, start and approve Glacier restores (ledger/restore_panel.py)."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ledger"))
sys.path.insert(0, str(ROOT / "scripts"))

from restore_panel import (  # noqa: E402
    estimate_usd,
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


REQ = {"game_key": "pkg", "tier": "Bulk", "reason": "disc.iso fails the Wasabi check"}


def awaiting(**over):
    r = {"id": 1, "game_ingest_id": 1, "state": "awaiting_approval", "tier": "Bulk", "reason": "x",
         "requested_by": ADMIN, "message": "Restoring 1 corrupted file. 2 files on Wasabi are fine and won't be restored.",
         "files": [{"bag": "b/p/", "rel": "data/disc.iso", "size": 4, "sha256": "a", "verdict": "corrupted",
                    "restorable": True, "note": "", "written": ""},
                   {"bag": "b/p/", "rel": "bag-info.txt", "size": 2, "sha256": "b", "verdict": "ok",
                    "restorable": False, "note": "", "written": ""}]}
    r.update(over)
    return r


def test_non_admin_cannot_request_or_approve():
    store = FakeStore()
    store.requests.append(awaiting())
    before = [dict(r) for r in store.requests]
    for path, form in [("/restore/request", REQ), ("/restore/approve", {"game_key": "pkg", "request_id": "1"}),
                       ("/restore/cancel", {"game_key": "pkg", "request_id": "1"}),
                       ("/restore/close-report", {"game_key": "pkg", "report_id": "1", "note": "ok"})]:
        assert post(store, path, user="someone@example.org", **form) == ("pkg", "fail", "Only admins can do this.")
    assert post(store, "/restore/approve", user="", game_key="pkg", request_id="1")[1] == "fail"
    assert store.requests == before


def test_admin_starts_a_restore():
    store = FakeStore()
    store.add_report(1, "broken", "a@example.org")
    assert post(store, "/restore/request", user=ADMIN, report_id="1", **REQ) == (
        "pkg", "ok", "Restore started. The job checks Wasabi within 15 minutes.")
    assert store.requests[-1]["state"] == "checking" and store.requests[-1]["problem_report_id"] == 1
    assert store.requests[-1]["requested_by"] == ADMIN


def test_admin_emails_match_case_insensitively():
    store = FakeStore()
    assert post(store, "/restore/request", user="Admin@Example.org", **REQ)[1] == "ok"


def test_request_refused_without_copy_or_with_open_request():
    assert post(FakeStore(has_copy=False), "/restore/request", user=ADMIN, **REQ) == (
        "pkg", "fail", "This package has no copy in Glacier yet.")
    store = FakeStore()
    store.requests.append(awaiting())
    assert post(store, "/restore/request", user=ADMIN, **REQ) == (
        "pkg", "fail", "A restore is already open for this package.")
    assert post(FakeStore(), "/restore/request", user=ADMIN, **{**REQ, "tier": "Expedited"})[1] == "fail"
    assert post(FakeStore(), "/restore/request", user=ADMIN, **{**REQ, "reason": ""})[1] == "fail"


def test_approve_only_from_awaiting_approval():
    store = FakeStore()
    store.requests.append(awaiting())
    assert post(store, "/restore/approve", user=ADMIN, game_key="pkg", request_id="1") == (
        "pkg", "ok", "Approved. The job writes the files to Wasabi within 15 minutes.")
    assert store.requests[0]["state"] == "approved" and store.requests[0]["approved_by"] == ADMIN
    assert post(store, "/restore/approve", user=ADMIN, game_key="pkg", request_id="1") == (
        "pkg", "fail", "This restore isn't waiting for approval any more.")


def test_cancel_refused_while_writing():
    store = FakeStore()
    store.requests.append(awaiting(state="writing"))
    assert post(store, "/restore/cancel", user=ADMIN, game_key="pkg", request_id="1")[1] == "fail"
    assert store.requests[0]["state"] == "writing"
    store.requests[0]["state"] = "restoring"
    assert post(store, "/restore/cancel", user=ADMIN, game_key="pkg", request_id="1") == (
        "pkg", "ok", "Restore cancelled.")


def test_admin_closes_a_report():
    store = FakeStore()
    store.add_report(1, "broken", "a@example.org")
    assert post(store, "/restore/close-report", user=ADMIN, game_key="pkg", report_id="1", note="fixed by hand") == (
        "pkg", "ok", "Report closed.")
    assert store.reports[0]["close_note"] == "fixed by hand"
    assert post(store, "/restore/close-report", user=ADMIN, game_key="pkg", report_id="1", note="x")[1] == "fail"


def test_bad_ids_are_refused():
    assert post(FakeStore(), "/restore/approve", user=ADMIN, game_key="pkg", request_id="abc")[1] == "fail"


def test_form_shows_hint_when_last_wasabi_check_passed():
    check = {"outcome": "pass", "checked_at": NOV14, "detail": "wasabi: 41 files"}
    html = render_restore_panel(state(last_wasabi_check=check), game_key="pkg", is_admin=True, has_copy=True,
                                total_bytes=4 * 10**9)
    assert ("The last Wasabi check, on 2026-11-14, passed, so these files may not need a restore. The restore job "
            "checks every file first and restores only the ones that are missing or corrupted.") in html
    assert 'action="/restore/request"' in html and "about $0.73" in html and "about $0.80" in html


def test_form_shows_a_failed_wasabi_check():
    check = {"outcome": "fail", "checked_at": NOV14, "detail": "wasabi: data/disc.iso differs"}
    html = render_restore_panel(state(last_wasabi_check=check), game_key="pkg", is_admin=True, has_copy=True,
                                total_bytes=10**9)
    assert "The last Wasabi check, on 2026-11-14, failed: wasabi: data/disc.iso differs" in html


def test_no_form_without_a_copy():
    html = render_restore_panel(state(), game_key="pkg", is_admin=True, has_copy=False, total_bytes=10**9)
    assert 'action="/restore/request"' not in html
    assert "This package has no copy in Glacier yet, so it can't be restored." in html


def test_estimate_for_a_4_gb_dvd():
    assert round(estimate_usd(4 * 10**9, "Bulk"), 2) == 0.73
    assert round(estimate_usd(4 * 10**9, "Standard"), 2) == 0.80


def test_awaiting_approval_shows_file_table_and_buttons():
    html = render_restore_panel(state(request=awaiting()), game_key="pkg", is_admin=True, has_copy=True,
                                total_bytes=10**9)
    assert "Waiting for approval" in html and "Restoring 1 corrupted file." in html
    assert "data/disc.iso" in html and "corrupted" in html
    assert "bag-info.txt" not in html and "1 file is fine." in html
    assert "Approving writes 1 file to Wasabi." in html
    assert 'action="/restore/approve"' in html and 'action="/restore/cancel"' in html
    assert 'action="/restore/request"' not in html


def test_non_admin_sees_status_but_no_buttons():
    html = render_restore_panel(state(request=awaiting()), game_key="pkg", is_admin=False, has_copy=True,
                                total_bytes=10**9)
    assert "Waiting for approval" in html
    assert 'action="/restore/approve"' not in html and 'action="/restore/cancel"' not in html


def test_finished_request_is_shown_as_the_last_restore():
    html = render_restore_panel(state(request=awaiting(state="not_needed", message="No file on Wasabi is missing "
                                                     "or corrupted. Nothing needs restoring.", files=[])),
                                game_key="pkg", is_admin=True, has_copy=True, total_bytes=10**9)
    assert "Last restore: Not needed" in html and 'action="/restore/request"' in html
