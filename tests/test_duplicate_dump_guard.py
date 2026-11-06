"""ingest-folder.py: duplicate-dump guard (primary image hash vs the ledger)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
pytest.importorskip("pyclamd")
pytest.importorskip("psycopg")
spec = importlib.util.spec_from_file_location("ingest_folder", ROOT / "scripts" / "ingest-folder.py")
ing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ing)


def _write(path: Path, size: int, byte: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(byte * size)
    return path


def test_primary_image_is_the_largest_image(tmp_path: Path) -> None:
    _write(tmp_path / "1.0.3" / "Game (Card ID Set) (3D2CA7E7).bin", 512)      # generic, tiny
    _write(tmp_path / "1.0.3" / "DMI.bin", 2048)
    _write(tmp_path / "Track 2.bin", 3 << 20)
    big = _write(tmp_path / "Track 1.bin", 5 << 20)
    _write(tmp_path / "!submissionInfo.txt", 9 << 20)                         # not an image
    assert ing.primary_dump_image(tmp_path) == big


def test_no_primary_image_for_small_or_missing_images(tmp_path: Path) -> None:
    _write(tmp_path / "DMI.bin", 2048)
    _write(tmp_path / "Scans" / "front.png", 5 << 20)
    assert ing.primary_dump_image(tmp_path) is None


class _Cur:
    def __init__(self, rows): self.rows, self.description = rows, [type("D", (), {"name": n}) for n in ("game_key", "status", "omeka_item_id", "package_storage_prefix")]
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, params): self.params = params
    def fetchall(self): return self.rows


class _Conn:
    def __init__(self, rows): self.rows, self.closed = rows, False
    def cursor(self): return _Cur(self.rows)
    def close(self): self.closed = True


def test_guard_refuses_a_preserved_image(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.delenv("HITSAVE_ALLOW_REINGEST", raising=False)
    _write(tmp_path / "FWD0512_2.iso", 2 << 20)
    conn = _Conn([("famitsu-wave-dvd-2005-12gatsu-gou-x", "complete", 26493, "dumps/x")])
    with pytest.raises(SystemExit) as e:
        ing.guard_duplicate_dump(conn, game_key="new-key", source=tmp_path, game_cfg={})
    assert e.value.code == ing.DUPLICATE_DUMP_EXIT and conn.closed
    out = capsys.readouterr()
    assert "DUPLICATE_OF=famitsu-wave-dvd-2005-12gatsu-gou-x" in out.out and "REFUSING duplicate dump" in out.err


def test_guard_passes_new_images_and_honours_override(tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path / "NEW.iso", 2 << 20)
    ing.guard_duplicate_dump(_Conn([]), game_key="k", source=tmp_path, game_cfg={})            # no match
    monkeypatch.setenv("HITSAVE_ALLOW_REINGEST", "1")
    ing.guard_duplicate_dump(_Conn([("dup", "complete", 1, "p")]), game_key="k", source=tmp_path, game_cfg={})
