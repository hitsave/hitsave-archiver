"""PRONOM format identification (Siegfried) recorded per file in inventory.json."""
from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

import format_identification as fid

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("build_bagit_package", ROOT / "scripts" / "build-bagit-package.py")
bbp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bbp)

HAVE_SF = shutil.which("sf") is not None
CFG = {"enabled": True, "binary": "sf"}

SF_JSON = {
    "siegfried": "1.11.9",
    "signature": "default.sig",
    "created": "2026-09-27T12:04:02+10:00",
    "identifiers": [{"name": "pronom", "details": "DROID_SignatureFile_V125.xml"}],
    "files": [
        {"filename": "{root}/notes.txt", "errors": "", "matches": [
            {"ns": "pronom", "id": "x-fmt/111", "format": "Plain Text File", "version": "",
             "mime": "text/plain", "basis": "extension match txt; text match ASCII", "warning": ""}]},
        {"filename": "{root}/scans/broken.pdf", "errors": "", "matches": [
            {"ns": "pronom", "id": "UNKNOWN", "format": "", "version": "", "mime": "", "basis": "",
             "warning": "no match; possibilities based on extension are fmt/14, fmt/15"}]},
        {"filename": "{root}/locked.bin", "errors": "open: permission denied", "matches": []},
    ],
}


def _fake_sf(tmp_path: Path, payload: dict, root: Path, exit_code: int = 0) -> str:
    out = tmp_path / "sf.json"
    out.write_text(json.dumps(payload).replace("{root}", str(root)))
    script = tmp_path / "fake-sf"
    script.write_text(f"#!/bin/sh\ncat {out}\nexit {exit_code}\n")
    script.chmod(0o755)
    return str(script)


def test_parses_matches_unknowns_and_errors(tmp_path: Path) -> None:
    root = tmp_path / "bag"
    root.mkdir()
    run = fid.identify_tree(root, {"enabled": True, "binary": _fake_sf(tmp_path, SF_JSON, root)})
    assert run.status == "ok"
    assert run.hits["notes.txt"].record() == {
        "puid": "x-fmt/111", "name": "Plain Text File", "mime": "text/plain",
        "basis": "extension match txt; text match ASCII",
    }
    unknown = run.hits["scans/broken.pdf"]
    assert unknown.puid is None
    assert unknown.record() == {"puid": None, "warning": "no match; possibilities based on extension are fmt/14, fmt/15"}
    assert run.hits["locked.bin"].record() == {"puid": None, "error": "open: permission denied"}
    assert run.record()["tool"] == "siegfried 1.11.9"
    assert run.record()["signature"] == "default.sig (2026-09-27T12:04:02+10:00; DROID_SignatureFile_V125.xml)"


def test_missing_binary_is_recorded_not_raised(tmp_path: Path) -> None:
    run = fid.identify_tree(tmp_path, {"enabled": True, "binary": str(tmp_path / "no-such-sf")})
    assert run.status == "failed" and run.hits == {}
    assert "no-such-sf" in run.error


def test_bad_exit_is_recorded_not_raised(tmp_path: Path) -> None:
    run = fid.identify_tree(tmp_path, {"enabled": True, "binary": _fake_sf(tmp_path, {}, tmp_path, exit_code=3)})
    assert run.status == "failed" and "exit 3" in run.error


def test_disabled_runs_nothing(tmp_path: Path) -> None:
    run = fid.identify_tree(tmp_path, {"enabled": False, "binary": str(tmp_path / "no-such-sf")})
    assert run.status == "disabled" and run.hits == {}


def test_inventory_carries_format_per_file_and_summary(tmp_path: Path) -> None:
    rows = [
        bbp.FileRow(rel="Game/notes.txt", path=tmp_path / "x", size=5, sha256="a" * 64, mime="text/plain"),
        bbp.FileRow(rel="Game/scans/broken.pdf", path=tmp_path / "y", size=9, sha256="b" * 64,
                    mime="application/pdf"),
    ]
    run = fid.FormatRun(
        status="ok", tool="siegfried 1.11.9", signature="default.sig", identified_at="2026-11-09T00:00:00Z",
        hits={
            "Game/notes.txt": fid.FormatHit(puid="x-fmt/111", name="Plain Text File", mime="text/plain"),
            "Game/scans/broken.pdf": fid.FormatHit(puid=None, warning="no match"),
        },
    )
    inv = bbp.build_inventory(rows, "data", formats=run)
    by_path = {f["path"]: f for f in inv["files"]}
    assert by_path["data/Game/notes.txt"]["format"]["puid"] == "x-fmt/111"
    assert by_path["data/Game/scans/broken.pdf"]["format"] == {"puid": None, "warning": "no match"}
    assert by_path["data/Game/notes.txt"]["mime"] == "text/plain"  # the viewer's field is unchanged
    assert inv["summary"]["by_format"] == {"x-fmt/111": 1, "unidentified": 1}
    assert inv["format_identification"] == {
        "status": "ok", "tool": "siegfried 1.11.9", "signature": "default.sig",
        "identified_at": "2026-11-09T00:00:00Z",
    }


def test_inventory_without_identification_is_unchanged(tmp_path: Path) -> None:
    rows = [bbp.FileRow(rel="a.txt", path=tmp_path / "x", size=1, sha256="a" * 64, mime="text/plain")]
    inv = bbp.build_inventory(rows, "data")
    assert "format" not in inv["files"][0]
    assert "format_identification" not in inv and "by_format" not in inv["summary"]


def test_failed_identification_is_recorded_in_inventory(tmp_path: Path) -> None:
    rows = [bbp.FileRow(rel="a.txt", path=tmp_path / "x", size=1, sha256="a" * 64, mime="text/plain")]
    run = fid.FormatRun(status="failed", error="sf: not found", identified_at="2026-11-09T00:00:00Z")
    inv = bbp.build_inventory(rows, "data", formats=run)
    assert "format" not in inv["files"][0]
    assert inv["format_identification"]["status"] == "failed"
    assert inv["format_identification"]["error"] == "sf: not found"


@pytest.mark.skipif(not HAVE_SF, reason="Siegfried (sf) not installed")
def test_real_siegfried_identifies_text_and_flags_unknown(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "readme.txt").write_text("hello world\n")
    (tmp_path / "blob.pdf").write_bytes(b"not really a pdf")
    run = fid.identify_tree(tmp_path, CFG)
    assert run.status == "ok", run.error
    assert run.hits["sub/readme.txt"].puid == "x-fmt/111"
    assert run.hits["blob.pdf"].puid is None
    assert run.tool.startswith("siegfried ")


@pytest.mark.skipif(not HAVE_SF, reason="Siegfried (sf) not installed")
def test_built_bag_records_a_format_for_every_payload_file(tmp_path: Path, monkeypatch) -> None:
    sip = tmp_path / "Some Game (Japan)"
    (sip / "Scans").mkdir(parents=True)
    (sip / "!submissionInfo.txt").write_text("info\n")
    (sip / ".checksum.md5").write_text("d41d8cd98f00b204e9800998ecf8427e  disc.iso\n")
    (sip / "Scans" / "front side.jpg").write_bytes(b"\xff\xd8\xff\xe0 truncated")
    identity = type("I", (), {})()
    for k, v in dict(package_id="pkg-1", package_uuid="u", relative_sip_path=sip.name, sip_fingerprint="f",
                     title_slug="t", sort_letter="S", omeka_item_title="T").items():
        setattr(identity, k, v)
    identity.external_identifier = lambda: "hitsave:pkg-1"
    monkeypatch.setattr(bbp, "load_packaging_config", lambda: {"bagit": {"preserve_source_folder_in_payload": True}})
    monkeypatch.setattr(bbp, "load_ingest_config", lambda: {"wasabi": {}})
    monkeypatch.setattr(bbp, "resolve_submission_source", lambda s: None)
    monkeypatch.setattr(bbp, "resolve_wasabi_bucket", lambda w, k: "b")
    monkeypatch.setattr(bbp, "load_preview_config", lambda: {})
    monkeypatch.setattr(bbp, "write_hitsave_previews", lambda *a, **k: None)
    monkeypatch.setattr(bbp, "identify_tree", lambda root: fid.identify_tree(root, CFG))
    inventory, _ = bbp.build_bagit_package(
        sip, tmp_path / "bag", identity=identity, agent_version="t", repository_code="hs",
        max_files=100, max_bytes=0,
    )
    assert inventory["format_identification"]["status"] == "ok"
    assert all("format" in f for f in inventory["files"]), inventory["files"]
    by_name = {Path(f["path"]).name: f["format"] for f in inventory["files"]}
    assert by_name["!submissionInfo.txt"]["puid"] == "x-fmt/111"
    on_disk = json.loads((tmp_path / "bag" / "metadata" / "inventory.json").read_text())
    assert on_disk["summary"]["by_format"] == inventory["summary"]["by_format"]
