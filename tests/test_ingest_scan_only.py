"""ingest-folder.py --scan-only, and the full ingest reusing that scan instead of scanning twice."""
from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
_spec = importlib.util.spec_from_file_location("ingest_folder", ROOT / "scripts" / "ingest-folder.py")
ingest_folder = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ingest_folder)

from premis_virus_check import VirusScanReport  # noqa: E402
from virus_scan_cache import report_path, save_report  # noqa: E402


def _report(tag: str) -> VirusScanReport:
    return VirusScanReport(
        scanned_at=tag, files_scanned=1, files_not_scanned=0, scanner_program="ClamAV", scanner_version="1"
    )


def _sip(tmp_path: Path) -> Path:
    sip = tmp_path / "ready" / "pkg"
    sip.mkdir(parents=True)
    (sip / "game.iso").write_bytes(b"x" * 10)
    return sip


def test_parse_args() -> None:
    assert ingest_folder.parse_args(["/r/pkg/game.ingest.yaml"]) == (Path("/r/pkg/game.ingest.yaml"), False)
    assert ingest_folder.parse_args(["/r/pkg/game.ingest.yaml", "--scan-only"]) == (Path("/r/pkg/game.ingest.yaml"), True)
    assert ingest_folder.parse_args(["--scan-only", "/g.yaml"]) == (Path("/g.yaml"), True)
    assert ingest_folder.parse_args([]) == (Path("/config/preservation/game.yml"), False)


def test_reuses_saved_scan_when_files_unchanged(tmp_path, capsys) -> None:
    sip = _sip(tmp_path)
    cache = report_path(tmp_path / "out", "pkg")
    save_report(cache, _report("first"), sip)
    scans = []
    got = ingest_folder.scan_or_reuse(None, sip, cache, scan=lambda c, s: scans.append(s) or _report("fresh"))
    assert got.scanned_at == "first" and scans == []
    assert "reusing the scan from first" in capsys.readouterr().out


def test_scans_when_nothing_saved_or_files_changed(tmp_path, capsys) -> None:
    sip = _sip(tmp_path)
    cache = report_path(tmp_path / "out", "pkg")
    fresh = lambda c, s: _report("fresh")  # noqa: E731
    assert ingest_folder.scan_or_reuse(None, sip, cache, scan=fresh).scanned_at == "fresh"
    assert capsys.readouterr().out == ""  # no saved scan is the normal manual-ingest case: no noise
    save_report(cache, _report("first"), sip)
    (sip / "readme.txt").write_text("added after the scan")
    assert ingest_folder.scan_or_reuse(None, sip, cache, scan=fresh).scanned_at == "fresh"
    assert "scanning again (files changed since the saved scan)" in capsys.readouterr().out


def test_main_wiring() -> None:
    src = inspect.getsource(ingest_folder.main)
    # scan-only always scans fresh, saves the report and stops before BagIt/Wasabi
    head, _, tail = src.partition("if scan_only:\n            save_report(")
    assert tail and "run_bagit_ingest" not in tail.split("return", 1)[0]
    assert "run_bagit_ingest" in tail
    assert "virus_scan = scan_sip_tree(clamd, source)" in head
    assert "scan_or_reuse(clamd, source, scan_cache)" in head
    # MobyGames enrichment waits for the real ingest; the gates run in both modes
    assert "if not scan_only:\n            # Metadata only; never blocks archiving.\n            run_moby_enrichment" in src
    assert src.index("guard_complete_package") < src.index("check_source_content_policy") < src.index("scan_sip_tree")
    # the saved report is removed once the package is preserved
    assert src.index("scan_cache.unlink(missing_ok=True)") > src.index("upsert_ledger(conn, **complete_kwargs)")
