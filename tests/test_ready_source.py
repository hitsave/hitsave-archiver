from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "remote-intake"))

import ready_source as rs  # noqa: E402


def _ready(root: Path, slug: str, block: dict | None) -> Path:
    d = root / slug
    d.mkdir(parents=True)
    if block is not None:
        (d / rs.MANIFEST_NAME).write_text(yaml.safe_dump({"game_key": slug, "redump": block}))
    return d


def test_absent(tmp_path: Path) -> None:
    assert rs.check_source(tmp_path / "redump-x", drive_folder_id="abc") == (rs.ABSENT, None)


def test_drive_match_and_mismatch(tmp_path: Path) -> None:
    d = _ready(tmp_path, "redump-fwd0307", {"drive_folder_id": "abc"})
    assert rs.check_source(d, drive_folder_id="abc")[0] == rs.MATCH
    assert rs.check_source(d, drive_folder_id="other")[0] == rs.MISMATCH
    assert rs.check_source(d, wasabi_prefix="F.Wave/FWD0307")[0] == rs.MISMATCH


def test_wasabi_prefix_normalised(tmp_path: Path) -> None:
    # intake records "<prefix>/<folder>"; an empty prefix yields "/<folder>".
    d = _ready(tmp_path, "redump-fwd0307", {"wasabi_source_prefix": "/FWD0307"})
    assert rs.check_source(d, wasabi_prefix="FWD0307")[0] == rs.MATCH


def test_same_folder_other_wasabi_root_is_mismatch(tmp_path: Path) -> None:
    d = _ready(tmp_path, "redump-fwd0307", {"wasabi_source_prefix": "FamitsuWaveDVD/FWD0307"})
    state, rec = rs.check_source(d, wasabi_prefix="F.Wave/FWD0307")
    assert state == rs.MISMATCH
    assert rs.describe(rec) == "wasabi:FamitsuWaveDVD/FWD0307"


def test_legacy_manifest_without_source_is_unknown(tmp_path: Path) -> None:
    d = _ready(tmp_path, "switch-aka", {})
    assert rs.check_source(d, drive_folder_id="abc")[0] == rs.UNKNOWN


def test_cli_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _ready(tmp_path, "redump-a", {"drive_folder_id": "abc"})
    _ready(tmp_path, "redump-legacy", {})
    base = ["check", "--ready-root", str(tmp_path)]
    assert rs.main(base + ["--slug", "redump-new", "--drive-folder-id", "x"]) == 0
    assert rs.main(base + ["--slug", "redump-a", "--drive-folder-id", "abc"]) == 0
    assert rs.main(base + ["--slug", "redump-a", "--drive-folder-id", "zzz"]) == 3
    assert "mismatch drive:abc" in capsys.readouterr().out
    assert rs.main(base + ["--slug", "redump-legacy", "--drive-folder-id", "x"]) == 4


def test_require_ready_root_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        rs.require_ready_root(tmp_path / "not-mounted")
    assert rs.require_ready_root(tmp_path) == tmp_path
