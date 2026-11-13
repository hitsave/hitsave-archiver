"""Ledger /ops: the page lists recent Windmill runs and has no row of script links."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ledger"))


def _load_app(tmp_path, monkeypatch):
    cfg = yaml.safe_load((ROOT / "config" / "ledger.yaml.example").read_text())
    (tmp_path / "database.yaml").write_text("postgres: {host: db, dbname: ledger, user: u, password: p}\n")
    cfg["database"]["config_file"] = str(tmp_path / "database.yaml")
    cfg["logs"]["generated_dir"] = str(tmp_path / "generated")
    cfg["ops"]["windmill_ui_url"] = "http://wm.example:8089"
    cfg["ops"]["windmill"]["api_url"] = "http://wm.example:8089/api"
    cfg["ops"]["windmill"]["token_file"] = str(tmp_path / "missing.token")
    (tmp_path / "ledger.yaml").write_text(yaml.safe_dump(cfg))
    monkeypatch.setenv("HITSAVE_LEDGER_CONFIG", str(tmp_path / "ledger.yaml"))
    sys.modules.pop("app", None)
    return importlib.import_module("app")


def test_ops_page_lists_runs_without_script_links(tmp_path, monkeypatch) -> None:
    app = _load_app(tmp_path, monkeypatch)
    run = {"id": "019a7f", "path": "f/hitsave/remote_intake_sweep", "success": True,
           "started_at": "2026-11-13T09:00:00Z"}
    monkeypatch.setattr(app, "fetch_completed_jobs", lambda **_: ([run], None))
    page = app.render_ops().decode()
    assert "/run/019a7f" in page
    assert "/scripts/get/" not in page
