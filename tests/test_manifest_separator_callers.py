"""Every script that rewrites a bag manifest keeps one separator style: the file's first line's."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import add_catalogue_scans_wasabi as acs
import apply_dvd_menu_bag_previews as dvd
import catalog_snapshot as cs
import premis_events as pe
from sync_dump_bag_metadata_from_ledger import update_manifest_digest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rescan = _load("rescan_sep", "rescan-virus-unscanned.py")
integrity = _load("integrity_sep", "check-integrity-wasabi.py")

# bagit-python tag manifest (one space), already mixed by an earlier two-space edit.
TAG = "aa bag-info.txt\nbb  metadata/premis/old.xml\ncc metadata/submission.json\n"


def uniform_one_space(text: str) -> bool:
    return all(len(line.split(" ")[1]) > 0 for line in text.splitlines() if line.strip())


def test_premis_add_tagmanifest_line() -> None:
    out = pe.add_tagmanifest_line(TAG, "metadata/premis/new.xml", "dd")
    assert out.endswith("dd metadata/premis/new.xml\n") and uniform_one_space(out)


def test_rescan_add_tagmanifest_line() -> None:
    out = rescan.add_tagmanifest_line(TAG, "metadata/premis/rescan.xml", "dd")
    assert out.endswith("dd metadata/premis/rescan.xml\n") and uniform_one_space(out)


def test_dvd_upsert_manifest_lines() -> None:
    out = dvd.upsert_manifest_lines(TAG, {"metadata/hitsave/x.jpg": "dd", "bag-info.txt": "ZZ"})
    assert out.startswith("ZZ bag-info.txt\n") and uniform_one_space(out)


def test_catalogue_scans_upsert_and_rename() -> None:
    assert uniform_one_space(acs.upsert_lines(TAG, {"metadata/x.txt": "dd"}))
    payload = "aa  data/a.tif\nbb  data/b.tif\n"
    assert acs.upsert_lines(payload, {"data/c.tif": "cc"}) == payload + "cc  data/c.tif\n"
    assert acs.rename_lines(payload, {"data/a.tif": "data/Scans/a.tif"}) == "aa  data/Scans/a.tif\nbb  data/b.tif\n"


def test_integrity_update_manifest_line() -> None:
    out = integrity.update_manifest_line(TAG, "metadata/submission.json", "ZZ")
    assert "ZZ metadata/submission.json\n" in out and uniform_one_space(out)


def test_sync_update_manifest_digest() -> None:
    out = update_manifest_digest(TAG, "bag-info.txt", "ZZ")
    assert out.startswith("ZZ bag-info.txt\n") and uniform_one_space(out)


def test_catalog_snapshot_tagmanifest_stays_uniform() -> None:
    snap = cs.build_snapshot({"o:id": 1, "o:is_public": True}, package_id="p", taken_at="2026-11-12T05:30:00Z",
                             names={})
    _, out = cs.stage_snapshot(TAG, snap, previous=None)
    assert uniform_one_space(out)

