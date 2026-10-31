"""ingest-folder.py must never silently overwrite (or mark failed) an already-complete package."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("ingest_folder", ROOT / "scripts" / "ingest-folder.py")
ingest_folder = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ingest_folder)


class _Cursor:
    def __init__(self, row):
        self.row = row

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *args):
        pass

    def fetchone(self):
        return self.row


class FakeConn:
    def __init__(self, row):
        self.row = row
        self.closed = False

    def cursor(self):
        return _Cursor(self.row)

    def close(self):
        self.closed = True


@pytest.fixture
def sip(tmp_path: Path) -> Path:
    d = tmp_path / "redump-fwd0307"
    d.mkdir()
    (d / "disc.iso").write_bytes(b"x" * 10)
    return d


@pytest.fixture(autouse=True)
def _flat_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    # Pin payload layout so the test doesn't depend on packaging.yaml.
    import preservation_common

    monkeypatch.setattr(
        preservation_common,
        "load_packaging_config",
        lambda: {"bagit": {"payload_dir": "data", "preserve_source_folder_in_payload": False}},
    )
    monkeypatch.delenv("HITSAVE_ALLOW_REINGEST", raising=False)


SAME = {"files": [{"path": "data/disc.iso", "size_bytes": 10}]}
OTHER = {"files": [{"path": "data/other.iso", "size_bytes": 99}]}


def _guard(row, sip: Path, cfg: dict | None = None) -> FakeConn:
    conn = FakeConn(row)
    ingest_folder.guard_complete_package(conn, game_key="pkg", source=sip, game_cfg=cfg or {})
    return conn


def test_new_or_unfinished_packages_proceed(sip: Path) -> None:
    assert not _guard(None, sip).closed
    assert not _guard(("failed", "/x", OTHER), sip).closed


def test_identical_complete_package_is_noop(sip: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        _guard(("complete", "/x", SAME), sip)
    assert exc.value.code == 0


def test_different_sip_for_complete_package_is_refused(sip: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        _guard(("complete", "/x", OTHER), sip)
    assert exc.value.code == ingest_folder.REINGEST_REFUSED_EXIT


def test_size_change_is_refused(sip: Path) -> None:
    changed = {"files": [{"path": "data/disc.iso", "size_bytes": 11}]}
    with pytest.raises(SystemExit) as exc:
        _guard(("complete", "/x", changed), sip)
    assert exc.value.code == ingest_folder.REINGEST_REFUSED_EXIT


def test_explicit_override(sip: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert not _guard(("complete", "/x", OTHER), sip, {"allow_reingest": True}).closed
    monkeypatch.setenv("HITSAVE_ALLOW_REINGEST", "1")
    assert not _guard(("complete", "/x", OTHER), sip).closed


def test_diff_reports_paths(sip: Path) -> None:
    diffs = ingest_folder.sip_inventory_diff(sip, OTHER)
    assert "new file data/disc.iso" in diffs
    assert "missing file data/other.iso" in diffs
    assert ingest_folder.sip_inventory_diff(sip, SAME) == []
