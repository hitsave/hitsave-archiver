from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import intake_source as src  # noqa: E402



def _manifest(d: Path, data: dict) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    (d / src.MANIFEST_NAME).write_text(yaml.safe_dump(data))
    return d


def test_ref_round_trip() -> None:
    for rec in ({"drive_folder_id": "1AbC"}, {"wasabi_prefix": "Weekly Famitsu/E3_2002"}):
        assert src.parse_ref(src.to_ref(rec)) == rec


def test_wasabi_prefix_normalised() -> None:
    assert src.to_ref({"wasabi_prefix": "/FWD0307/"}) == "wasabi:FWD0307"
    assert src.parse_ref("wasabi:/FWD0307/") == {"wasabi_prefix": "FWD0307"}
    assert src.current_ref(wasabi_prefix="/FWD0307") == "wasabi:FWD0307"


def test_parse_rejects_junk() -> None:
    for junk in (None, "", "drive:", "wasabi:/", "s3:abc", "abc"):
        assert src.parse_ref(junk) is None


def test_all_lane_blocks(tmp_path: Path) -> None:
    for i, key in enumerate(("redump", "no_intro", "others")):
        d = _manifest(tmp_path / str(i), {"game_key": "x", key: {"drive_folder_id": f"id{i}"}})
        assert src.recorded_source(d) == {"drive_folder_id": f"id{i}"}


def test_no_manifest_vs_no_source(tmp_path: Path) -> None:
    assert src.recorded_source(tmp_path / "missing") is None
    d = _manifest(tmp_path / "legacy", {"game_key": "x", "redump": {"verified_image": "a.iso"}})
    assert src.recorded_source(d) == {}
    assert src.to_ref(src.recorded_source(d)) is None


def test_local_source() -> None:
    ref = "local:/tank/video/dumps/Switch/Hit Save/Submitted/Aka"
    rec = src.parse_ref(ref)
    assert rec == {"local_path": "/tank/video/dumps/Switch/Hit Save/Submitted/Aka"}
    assert src.to_ref(rec) == ref
    assert src.parse_ref("local:relative/path") is None
    assert src.parse_ref("local:/") is None
