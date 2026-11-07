"""Scan report saved by `ingest-folder.py --scan-only` and reused by the ingest that follows."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from clamav_full_scan import FileScan  # noqa: E402
from premis_virus_check import VirusScanReport, build_virus_check_premis_xml  # noqa: E402
from virus_scan_cache import load_matching_report, report_path, save_report  # noqa: E402

T0 = 1_800_000_000.0


def _sip(tmp_path: Path) -> Path:
    sip = tmp_path / "ready" / "pkg"
    (sip / "disc").mkdir(parents=True)
    (sip / "disc" / "game.iso").write_bytes(b"x" * 100)
    (sip / ".intake-lane").write_text("redump\n")
    return sip


def _report(sip: Path) -> VirusScanReport:
    return VirusScanReport(
        scanned_at="2027-01-15T08:00:00Z",
        files_scanned=2,
        files_not_scanned=0,
        scanner_program="ClamAV",
        scanner_version="ClamAV 1.4.3/27500",
        virus_definitions="27500",
        sip_root=str(sip),
        file_scans=[FileScan(rel="disc/game.iso", size=100, method="image", streams=3, notes=["udf"], sha256="ab")],
    )


def _save(tmp_path: Path, sip: Path) -> Path:
    path = report_path(tmp_path / "output", "pkg-key")
    save_report(path, _report(sip), sip, now=lambda: T0)
    return path


def test_round_trip_reuses_the_saved_report(tmp_path) -> None:
    sip = _sip(tmp_path)
    path = _save(tmp_path, sip)
    assert path == tmp_path / "output" / "virus-scan" / "pkg-key.json"
    report, why = load_matching_report(path, sip, now=lambda: T0 + 600)
    assert why == "" and report == _report(sip)
    assert isinstance(report.file_scans[0], FileScan)
    # the PREMIS event is the same as from the original scan
    assert _premis(report) == _premis(_report(sip))


def _premis(report: VirusScanReport) -> str:
    xml = build_virus_check_premis_xml(
        report, package_id="pkg-key", package_uuid="u", repository_code="HS", linking_agent="ingest-folder.py"
    )
    return re.sub(r"<premis:eventIdentifierValue>[^<]*<", "<", xml)  # a fresh UUID each time


def test_missing_or_unreadable(tmp_path) -> None:
    sip = _sip(tmp_path)
    path = report_path(tmp_path / "output", "pkg-key")
    assert load_matching_report(path, sip) == (None, "no saved scan")
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    report, why = load_matching_report(path, sip)
    assert report is None and "unreadable" in why


def test_rescan_when_files_change(tmp_path) -> None:
    sip = _sip(tmp_path)
    path = _save(tmp_path, sip)
    (sip / "disc" / "extra.txt").write_text("new")
    assert load_matching_report(path, sip, now=lambda: T0)[1] == "files changed since the saved scan"

    sip2 = _sip(tmp_path / "b")
    path2 = _save(tmp_path / "b", sip2)
    iso = sip2 / "disc" / "game.iso"
    st = iso.stat()
    os.utime(iso, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))  # same size, rewritten
    assert load_matching_report(path2, sip2, now=lambda: T0)[1] == "files changed since the saved scan"


def test_rescan_when_too_old_or_other_folder(tmp_path) -> None:
    sip = _sip(tmp_path)
    path = _save(tmp_path, sip)
    report, why = load_matching_report(path, sip, now=lambda: T0 + 25 * 3600)
    assert report is None and "too old" in why
    other = _sip(tmp_path / "other")
    report, why = load_matching_report(path, other, now=lambda: T0)
    assert report is None and "another folder" in why


def test_old_format_is_ignored(tmp_path) -> None:
    sip = _sip(tmp_path)
    path = _save(tmp_path, sip)
    data = json.loads(path.read_text())
    data["version"] = 0
    path.write_text(json.dumps(data))
    assert load_matching_report(path, sip, now=lambda: T0) == (None, "saved scan has an old format")
