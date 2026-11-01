"""Files left out of the BagIt payload by policy must be recorded, not silently dropped."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from preservation_common import should_skip, skip_reason

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("build_bagit_package", ROOT / "scripts" / "build-bagit-package.py")
bbp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bbp)


def _sip(tmp_path: Path) -> Path:
    sip = tmp_path / "Some Game"
    (sip / "scans" / "_ocr_crop_debug").mkdir(parents=True)
    (sip / "disc.iso").write_bytes(b"iso")
    (sip / "scans" / "front.jpg").write_bytes(b"jpg")
    (sip / ".DS_Store").write_bytes(b"ds")
    (sip / "scans" / "._front.jpg").write_bytes(b"appledouble")
    (sip / "scans" / "desktop.ini").write_bytes(b"ini")
    (sip / "scans" / "_ocr_crop_debug" / "crop.png").write_bytes(b"png")
    return sip


def test_skip_reasons() -> None:
    assert skip_reason(Path("a/.DS_Store")) == "dotfile"
    assert skip_reason(Path("a/desktop.ini")) == "desktop.ini"
    assert skip_reason(Path("a/_ocr_crop_debug/x.png")) == "ocr debug output"
    assert skip_reason(Path("a/disc.iso")) is None
    assert should_skip(Path("a/.hidden")) and not should_skip(Path("a/disc.iso"))


def test_collect_files_reports_excluded(tmp_path: Path) -> None:
    sip = _sip(tmp_path)
    excluded: list[dict] = []
    rows = bbp.collect_files(sip, 100, 0, excluded=excluded)
    assert sorted(r.rel for r in rows) == ["disc.iso", "scans/front.jpg"]
    assert {(e["path"], e["reason"]) for e in excluded} == {
        (".DS_Store", "dotfile"),
        ("scans/._front.jpg", "dotfile"),
        ("scans/desktop.ini", "desktop.ini"),
        ("scans/_ocr_crop_debug/crop.png", "ocr debug output"),
    }
    assert all(e["size_bytes"] > 0 for e in excluded)


def test_inventory_records_excluded(tmp_path: Path) -> None:
    sip = _sip(tmp_path)
    excluded: list[dict] = []
    rows = bbp.collect_files(sip, 100, 0, excluded=excluded)
    inv = bbp.build_inventory(rows, "data", excluded)
    assert inv["summary"]["excluded_count"] == 4
    assert len(inv["excluded_from_payload"]) == 4
    assert inv["summary"]["file_count"] == 2


def test_inventory_unchanged_when_nothing_excluded(tmp_path: Path) -> None:
    sip = tmp_path / "Clean"
    sip.mkdir()
    (sip / "disc.iso").write_bytes(b"iso")
    excluded: list[dict] = []
    inv = bbp.build_inventory(bbp.collect_files(sip, 100, 0, excluded=excluded), "data", excluded)
    assert "excluded_from_payload" not in inv
    assert "excluded_count" not in inv["summary"]


def test_collect_files_without_excluded_arg_still_works(tmp_path: Path) -> None:
    rows = bbp.collect_files(_sip(tmp_path), 100, 0)
    assert len(rows) == 2
