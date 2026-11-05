"""DVD menu stills at intake: build-bagit hook selection and the extractor's package mode."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "dvd-menu-preview" / "scripts"))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_menu_still_only_for_dvd_video(tmp_path: Path, monkeypatch) -> None:
    import pytest

    pytest.importorskip("bagit")
    build = _load("build_bagit_package", ROOT / "scripts" / "build-bagit-package.py")
    monkeypatch.setenv("HITSAVE_DVD_MENU_DIR", str(tmp_path))
    source = Path("/data/intake/ready/dvdvideo-gamewave-x")
    (tmp_path / source.name).mkdir()
    still = tmp_path / source.name / "menu_preview.jpg"
    still.write_bytes(b"jpg")
    dvd = SimpleNamespace(dump_system="DVD-Video")
    assert build.dvd_menu_still(source, dvd) == still
    assert build.dvd_menu_still(source, SimpleNamespace(dump_system="Sony PlayStation 2")) is None
    assert build.dvd_menu_still(source, None) is None
    still.unlink()
    assert build.dvd_menu_still(source, dvd) is None


def _extractor():
    return _load("extract_dvd_menu_preview", ROOT / "dvd-menu-preview" / "scripts" / "extract_dvd_menu_preview.py")


def test_package_mode_skips_non_dvd_video(tmp_path: Path) -> None:
    ext = _extractor()
    pkg = tmp_path / "ready" / "ps2-x"
    pkg.mkdir(parents=True)
    (pkg / "!submissionInfo.txt").write_text("Common Disc Info:\n\tSystem: Sony PlayStation 2\n")
    (pkg / "x.iso").write_bytes(b"x")
    out = tmp_path / "out"
    (out).mkdir()
    (out / "menu_preview.jpg").write_bytes(b"stale")
    result = ext.run_package(pkg, out, {})
    assert result["status"] == "not_dvd_video" and "Sony PlayStation 2" in result["reason"]
    assert not (out / "menu_preview.jpg").exists()          # stale still removed
    assert json.loads((out / "result.json").read_text())["status"] == "not_dvd_video"


def test_package_mode_needs_one_iso(tmp_path: Path) -> None:
    ext = _extractor()
    pkg = tmp_path / "ready" / "dvdvideo-x"
    pkg.mkdir(parents=True)
    (pkg / "!submissionInfo.txt").write_text("Common Disc Info:\n\tSystem: DVD-Video\n")
    assert ext.run_package(pkg, tmp_path / "out", {})["status"] == "skipped"
    assert ext.submission_system("\tSystem: DVD-Video\n\tMedia Type: DVD-ROM-5") == "DVD-Video"
