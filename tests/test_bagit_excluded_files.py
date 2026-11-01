"""Bags include every file in the submission (policy since 2026-11-01). Nothing is dropped;
any future exclusion would be recorded in inventory.json."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from preservation_common import legacy_skip_reason, should_skip, skip_reason

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("build_bagit_package", ROOT / "scripts" / "build-bagit-package.py")
bbp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bbp)
_spec2 = importlib.util.spec_from_file_location("ingest_folder", ROOT / "scripts" / "ingest-folder.py")
ingest_folder = importlib.util.module_from_spec(_spec2)
_spec2.loader.exec_module(ingest_folder)


def _sip(tmp_path: Path) -> Path:
    sip = tmp_path / "Some Game"
    (sip / "scans" / "_ocr_crop_debug").mkdir(parents=True)
    (sip / "disc.iso").write_bytes(b"iso")
    (sip / "scans" / "front.jpg").write_bytes(b"jpg")
    (sip / ".DS_Store").write_bytes(b"ds")
    (sip / ".checksum.md5").write_bytes(b"d41d8cd98f00b204e9800998ecf8427e  disc.iso\n")
    (sip / "scans" / "desktop.ini").write_bytes(b"ini")
    (sip / "scans" / "_ocr_crop_debug" / "crop.png").write_bytes(b"png")
    return sip


def test_nothing_is_skipped_any_more() -> None:
    for p in ("a/.DS_Store", "a/.checksum.md5", "a/desktop.ini", "a/_ocr_crop_debug/x.png", "a/disc.iso"):
        assert skip_reason(Path(p)) is None and not should_skip(Path(p))


def test_legacy_reasons_still_recognised() -> None:
    assert legacy_skip_reason(Path("a/.DS_Store")) == "dotfile"
    assert legacy_skip_reason(Path("a/desktop.ini")) == "desktop.ini"
    assert legacy_skip_reason(Path("a/_ocr_crop_debug/x.png")) == "ocr debug output"
    assert legacy_skip_reason(Path("a/disc.iso")) is None


def test_bag_payload_includes_every_file(tmp_path: Path) -> None:
    excluded: list[dict] = []
    rows = bbp.collect_files(_sip(tmp_path), 100, 0, excluded=excluded)
    assert sorted(r.rel for r in rows) == [
        ".DS_Store", ".checksum.md5", "disc.iso",
        "scans/_ocr_crop_debug/crop.png", "scans/desktop.ini", "scans/front.jpg",
    ]
    assert excluded == []
    inv = bbp.build_inventory(rows, "data", excluded)
    assert "excluded_from_payload" not in inv and inv["summary"]["file_count"] == 6


def test_guard_treats_legacy_excluded_files_as_identical(tmp_path: Path, monkeypatch) -> None:
    sip = _sip(tmp_path)
    # Preserved under the old policy: only the non-legacy files are in the inventory.
    preserved = {"files": [
        {"path": "data/disc.iso", "size_bytes": 3},
        {"path": "data/scans/front.jpg", "size_bytes": 3},
    ]}
    import preservation_common
    monkeypatch.setattr(preservation_common, "load_packaging_config", lambda: {"bagit": {}})
    assert ingest_folder.sip_inventory_diff(sip, preserved) == []
    # A genuinely new (non-legacy) file is still a difference.
    (sip / "scans" / "back.jpg").write_bytes(b"new")
    assert ingest_folder.sip_inventory_diff(sip, preserved) == ["new file data/scans/back.jpg"]
