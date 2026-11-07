"""DVD menu stills at intake: build-bagit hook selection (the extractor lives in private hitsave/dvd-menu-preview)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


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


def test_contact_sheet_when_the_disc_has_no_menu(tmp_path: Path, monkeypatch) -> None:
    import pytest

    pytest.importorskip("bagit")
    build = _load("build_bagit_package", ROOT / "scripts" / "build-bagit-package.py")
    monkeypatch.setenv("HITSAVE_DVD_MENU_DIR", str(tmp_path))
    source = Path("/data/intake/ready/dvd-video-wesker-s-report")
    (tmp_path / source.name).mkdir()
    sheet = tmp_path / source.name / "contact_sheet.jpg"
    sheet.write_bytes(b"jpg")
    dvd = SimpleNamespace(dump_system="DVD-Video")
    assert build.dvd_menu_still(source, dvd) == sheet
    assert build.dvd_menu_kind(sheet) == "contact_sheet"
    menu = tmp_path / source.name / "menu_preview.jpg"
    menu.write_bytes(b"jpg")  # a real menu wins
    assert build.dvd_menu_still(source, dvd) == menu and build.dvd_menu_kind(menu) == "menu"
