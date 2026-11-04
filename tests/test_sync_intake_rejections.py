"""sync_intake_rejections: the ledger table mirrors intake's failed/ folder."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
_spec = importlib.util.spec_from_file_location(
    "sync_intake_rejections", ROOT / "scripts/remote-intake/sync_intake_rejections.py")
sync_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync_mod)


def _fail(root: Path, slug: str, text: str | None, mtime: int) -> None:
    d = root / slug
    d.mkdir(parents=True)
    if text is not None:
        (d / "error.txt").write_text(text)
        os.utime(d / "error.txt", (mtime, mtime))


def test_scan_reads_first_line_and_lane(tmp_path: Path) -> None:
    _fail(tmp_path, "nointro-aka", "no-intro/ lane needs a No-Intro dumpinfo.txt\nmore detail", 1_000)
    _fail(tmp_path, "redump-disc", None, 2_000)
    _fail(tmp_path, "legacy-x", "boom", 3_000)
    (tmp_path / "stray.txt").write_text("not a package")
    got = {r.slug: r for r in sync_mod.scan(tmp_path)}
    assert set(got) == {"nointro-aka", "redump-disc", "legacy-x"}
    assert got["nointro-aka"].reason == "no-intro/ lane needs a No-Intro dumpinfo.txt"
    assert got["nointro-aka"].lane == "no-intro" and got["legacy-x"].lane is None
    assert got["redump-disc"].reason == "(no error.txt)"


class _Conn:
    def __init__(self):
        self.calls = []

    def transaction(self):
        conn = self

        class _T:
            def __enter__(self):
                conn.calls.append(("BEGIN",))

            def __exit__(self, *exc):
                conn.calls.append(("COMMIT",))
                return False
        return _T()

    def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split())[:40], params))

        class _R:
            rowcount = 2
        return _R()


def test_sync_replaces_table_in_one_transaction(tmp_path: Path) -> None:
    _fail(tmp_path, "others-press-kit", "boom", 1_000)
    conn = _Conn()
    n, removed = sync_mod.sync(conn, sync_mod.scan(tmp_path))
    assert (n, removed) == (1, 2)
    kinds = [c[0].split()[0] for c in conn.calls]
    assert kinds == ["BEGIN", "CREATE", "DELETE", "INSERT", "COMMIT"]
    assert conn.calls[2][1] == (["others-press-kit"],)


def test_missing_folder_changes_nothing(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["x", "--failed-dir", str(tmp_path / "not-mounted")])
    monkeypatch.setattr(sync_mod, "pg_connect", lambda: (_ for _ in ()).throw(AssertionError("no DB")))
    assert sync_mod.main() == 0
    assert "left unchanged" in capsys.readouterr().err


def test_lane_file_wins_for_type_based_slugs(tmp_path: Path) -> None:
    _fail(tmp_path, "dvd-video-famitsu-wave", "checksum mismatch", 1_000)
    (tmp_path / "dvd-video-famitsu-wave" / sync_mod.LANE_FILE).write_text("redump\n")
    _fail(tmp_path, "switch-aka", "bad xml", 1_000)  # no lane file, no lane prefix
    _fail(tmp_path, "others-usb", "x", 1_000)        # old failure: lane from the prefix
    got = {r.slug: r.lane for r in sync_mod.scan(tmp_path)}
    assert got == {"dvd-video-famitsu-wave": "redump", "switch-aka": None, "others-usb": "others"}
