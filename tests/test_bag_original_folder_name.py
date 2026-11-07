"""Bags keep the contributor's original folder name (data/<original name>/), not intake's
ready/<slug> staging name, and intake's own files go to metadata/intake/, not the payload."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import yaml

from preservation_common import original_folder_name, safe_folder_name, sip_layout

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("build_bagit_package", ROOT / "scripts" / "build-bagit-package.py")
bbp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bbp)
_spec2 = importlib.util.spec_from_file_location("ingest_folder", ROOT / "scripts" / "ingest-folder.py")
ingest_folder = importlib.util.module_from_spec(_spec2)
_spec2.loader.exec_module(ingest_folder)

ORIGINAL = "Making of Fahrenheit, The (Japan)"
GAME_CFG = {"ingest_kind": "dump", "handoff_manifest": "hitsave-submission.yaml"}


def _staged(tmp_path: Path, *, block: str = "redump", key: str = "package_folder_name", name: str = ORIGINAL) -> Path:
    sip = tmp_path / "ready" / "dvd-video-making-of-fahrenheit-the-japan"
    (sip / "Scans").mkdir(parents=True)
    (sip / "disc.iso").write_bytes(b"iso")
    (sip / "!submissionInfo.txt").write_text("info")
    (sip / "Scans" / "front.jpg").write_bytes(b"jpg")
    (sip / "hitsave-submission.yaml").write_text(
        yaml.safe_dump({"game_key": "dvd-video-making-of-fahrenheit-the-japan", block: {key: name}})
    )
    (sip / "game.ingest.yaml").write_text(yaml.safe_dump(GAME_CFG))
    return sip


def test_layout_uses_the_recorded_original_name(tmp_path) -> None:
    sip = _staged(tmp_path)
    layout = sip_layout(sip, GAME_CFG)
    assert layout.folder == ORIGINAL
    assert layout.staging_folder == "dvd-video-making-of-fahrenheit-the-japan"
    assert set(layout.intake_files) == {"game.ingest.yaml", "hitsave-submission.yaml"}
    assert sip_layout(_staged(tmp_path / "d", block="redump", key="drive_folder_name"), GAME_CFG).folder == ORIGINAL
    assert sip_layout(_staged(tmp_path / "o", block="others"), GAME_CFG).folder == ORIGINAL


def test_layout_without_handoff_keeps_the_folder_as_is(tmp_path) -> None:
    sip = _staged(tmp_path)
    layout = sip_layout(sip, {"ingest_kind": "press_material"})  # press batches, portable zips
    assert layout.folder == sip.name and layout.intake_files == ()


def test_unsafe_original_name_falls_back_to_the_staging_name(tmp_path) -> None:
    sip = _staged(tmp_path, name="AC/DC Live")
    assert sip_layout(sip, GAME_CFG).folder == sip.name
    assert not safe_folder_name("..") and not safe_folder_name(" x") and not safe_folder_name("a\nb")
    assert safe_folder_name("Tom's Game: Director's Cut (Europe) (En,Fr) [!]")
    assert original_folder_name({"no_intro": {"package_folder_name": "X"}}) == "X"
    assert original_folder_name({"redump": {}}) is None


def test_payload_uses_original_name_and_leaves_intake_files_out(tmp_path) -> None:
    sip = _staged(tmp_path)
    rows = bbp.collect_files(sip, 100, 0, preserve_source_folder=True, layout=sip_layout(sip, GAME_CFG))
    assert sorted(r.rel for r in rows) == [
        f"{ORIGINAL}/!submissionInfo.txt", f"{ORIGINAL}/Scans/front.jpg", f"{ORIGINAL}/disc.iso",
    ]


def test_built_bag_has_original_folder_and_intake_metadata(tmp_path, monkeypatch) -> None:
    sip = _staged(tmp_path)
    identity = type("I", (), {})()
    for k, v in dict(package_id="pkg-1", package_uuid="u", relative_sip_path=sip.name, sip_fingerprint="f",
                     title_slug="t", sort_letter="M", omeka_item_title="T").items():
        setattr(identity, k, v)
    identity.external_identifier = lambda: "hitsave:pkg-1"
    monkeypatch.setattr(bbp, "load_packaging_config", lambda: {"bagit": {"preserve_source_folder_in_payload": True}})
    monkeypatch.setattr(bbp, "load_ingest_config", lambda: {"wasabi": {}})
    monkeypatch.setattr(bbp, "resolve_submission_source", lambda s: None)
    monkeypatch.setattr(bbp, "resolve_wasabi_bucket", lambda w, k: "b")
    monkeypatch.setattr(bbp, "load_preview_config", lambda: {})
    monkeypatch.setattr(bbp, "write_hitsave_previews", lambda *a, **k: None)
    bag = tmp_path / "bag"
    inventory, submission = bbp.build_bagit_package(
        sip, bag, identity=identity, agent_version="t", repository_code="hs", max_files=100, max_bytes=0,
        layout=sip_layout(sip, GAME_CFG),
    )
    assert (bag / "data" / ORIGINAL / "disc.iso").is_file()
    assert not (bag / "data" / ORIGINAL / "game.ingest.yaml").exists()
    assert (bag / "metadata" / "intake" / "hitsave-submission.yaml").is_file()
    assert (bag / "metadata" / "intake" / "game.ingest.yaml").is_file()
    tagmanifest = (bag / "tagmanifest-sha256.txt").read_text()
    assert "metadata/intake/hitsave-submission.yaml" in tagmanifest
    assert all(f["path"].startswith(f"data/{ORIGINAL}/") for f in inventory["files"])
    assert inventory["summary"]["file_count"] == 3
    assert submission["source_folder_name"] == ORIGINAL
    assert submission["staging_folder_name"] == sip.name
    assert json.loads((bag / "metadata" / "submission.json").read_text())["source_folder_name"] == ORIGINAL


def _inventory(paths: dict[str, int]) -> dict:
    return {"files": [{"path": p, "size_bytes": n} for p, n in paths.items()]}


def test_reingest_guard_matches_both_old_and_new_bag_layouts(tmp_path, monkeypatch) -> None:
    import preservation_common

    monkeypatch.setattr(
        preservation_common, "load_packaging_config", lambda: {"bagit": {"preserve_source_folder_in_payload": True}}
    )
    sip = _staged(tmp_path)
    files = {"!submissionInfo.txt": 4, "Scans/front.jpg": 3, "disc.iso": 3}
    new = _inventory({f"data/{ORIGINAL}/{p}": n for p, n in files.items()})
    assert ingest_folder.sip_inventory_diff(sip, new, GAME_CFG) == []
    intake = {p: (sip / p).stat().st_size for p in ("game.ingest.yaml", "hitsave-submission.yaml")}
    old = _inventory({f"data/{sip.name}/{p}": n for p, n in {**files, **intake}.items()})
    assert ingest_folder.sip_inventory_diff(sip, old, GAME_CFG) == []
    (sip / "Scans" / "back.jpg").write_bytes(b"new")
    assert ingest_folder.sip_inventory_diff(sip, new, GAME_CFG) == [f"new file data/{ORIGINAL}/Scans/back.jpg"]
