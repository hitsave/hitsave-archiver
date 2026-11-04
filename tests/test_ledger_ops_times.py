"""Ledger /ops: Windmill runs show when they ran (started_at), not when the job was queued."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ledger"))

import windmill_client  # noqa: E402


class _Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


def _jobs(monkeypatch, row):
    monkeypatch.setattr(windmill_client.requests, "get", lambda *a, **k: _Resp([row]))
    jobs, err = windmill_client.fetch_completed_jobs(api_base="http://wm/api", workspace="w", token="t")
    assert err is None
    return jobs


def test_started_at_wins_over_created_at(monkeypatch) -> None:
    # A scheduled job is created when the previous run starts: created 15:30, ran 16:00.
    jobs = _jobs(monkeypatch, {
        "id": "01a1a72e",
        "script_path": "f/hitsave/remote_intake_sweep",
        "success": True,
        "created_at": "2026-11-04T15:30:07.388421Z",
        "started_at": "2026-11-04T16:00:00.012345Z",
    })
    assert jobs[0]["started_at"] == "2026-11-04T16:00:00.012345Z"
    assert "created_at" not in jobs[0]


def test_falls_back_to_created_at(monkeypatch) -> None:
    jobs = _jobs(monkeypatch, {"id": "x", "script_path": "f/x", "created_at": "2026-11-04T15:30:07Z"})
    assert jobs[0]["started_at"] == "2026-11-04T15:30:07Z"


def test_utc_display() -> None:
    u = windmill_client.utc_display
    assert u("2026-11-04T16:00:00.012345Z") == "2026-11-04 16:00:00"
    assert u("2026-11-04T11:00:00-05:00") == "2026-11-04 16:00:00"
    assert u("") == ""
    assert u("not a time") == "not a time"
