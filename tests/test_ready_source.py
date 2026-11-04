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


# --- ledger first (game_ingest.intake_source) -------------------------------------------------

def test_ledger_decides_without_ready_dir() -> None:
    assert rs.check_source(None, drive_folder_id="abc", ledger_source="drive:abc")[0] == rs.MATCH
    state, rec = rs.check_source(None, drive_folder_id="abc", ledger_source="wasabi:F/FWD0307")
    assert state == rs.MISMATCH and rec == {"wasabi_prefix": "F/FWD0307"}


def test_ledger_overrides_manifest(tmp_path: Path) -> None:
    d = _ready(tmp_path, "redump-fwd0307", {"drive_folder_id": "abc"})
    # ready/ says this package, the ledger says another one: the ledger wins.
    assert rs.check_source(d, drive_folder_id="abc", ledger_source="drive:other")[0] == rs.MISMATCH


def test_unusable_ledger_value_falls_back_to_manifest(tmp_path: Path) -> None:
    d = _ready(tmp_path, "redump-fwd0307", {"drive_folder_id": "abc"})
    for junk in ("", None, "garbage"):
        assert rs.check_source(d, drive_folder_id="abc", ledger_source=junk)[0] == rs.MATCH


def test_others_lane_manifest_is_read(tmp_path: Path) -> None:
    d = tmp_path / "others-usb-1"
    d.mkdir()
    (d / rs.MANIFEST_NAME).write_text(yaml.safe_dump({"others": {"drive_folder_id": "o1"}}))
    assert rs.check_source(d, drive_folder_id="o1")[0] == rs.MATCH
    assert rs.check_source(d, drive_folder_id="o2")[0] == rs.MISMATCH


def test_cli_ledger_source_needs_no_ready_root(tmp_path: Path, capsys) -> None:
    missing = tmp_path / "no-ready-here"
    args = ["check", "--slug", "redump-x", "--drive-folder-id", "abc", "--ready-root", str(missing)]
    assert rs.main(args + ["--ledger-source", "drive:abc"]) == 0
    assert capsys.readouterr().out.strip() == rs.MATCH
    assert rs.main(args + ["--ledger-source", "drive:zzz"]) == 3
    assert capsys.readouterr().out.strip() == "mismatch drive:zzz"
    with pytest.raises(SystemExit):  # no ledger value: ready/ is required (fails closed)
        rs.main(args)
