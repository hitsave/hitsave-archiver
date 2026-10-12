#!/usr/bin/env python3
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from dump_classify import classify_dump_sip  # noqa: E402

GDRIVE_EXAMPLE = ROOT.parent / "hitsave-gdrive-intake" / "examples" / "!submissionInfo.txt"


def test_redump_sidecar_classifies_system_and_category() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        sip = Path(tmp)
        assert GDRIVE_EXAMPLE.is_file(), f"missing fixture {GDRIVE_EXAMPLE}"
        shutil.copy(GDRIVE_EXAMPLE, sip / "!submissionInfo.txt")
        clf = classify_dump_sip(sip, package_id="famitsu-wave-dvd-01947c2a-test")
        assert clf.dump_class == "redump"
        assert clf.dump_system == "DVD-Video"
        assert clf.dump_category == "Coverdiscs"
        assert clf.package_storage_prefix == (
            "dumps/DVD-Video/Coverdiscs/famitsu-wave-dvd-01947c2a-test"
        )


def test_missing_sidecar_is_other() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        sip = Path(tmp)
        (sip / "game.iso").write_bytes(b"x")
        clf = classify_dump_sip(sip, package_id="pc-folder-01947c2a-test")
        assert clf.dump_class == "other"
        assert clf.dump_system is None
        assert clf.package_storage_prefix == "dumps/Others/pc-folder-01947c2a-test"


def test_ingest_kind_dump_alias() -> None:
    from ingest_kind import resolve_ingest_kind, storage_profile_for_kind  # noqa: E402

    assert resolve_ingest_kind({"ingest_kind": "dump"}) == "dump"
    assert resolve_ingest_kind({"ingest_kind": "game_dump"}) == "dump"
    assert resolve_ingest_kind({"ingest_kind": "disc_dump"}) == "dump"
    assert storage_profile_for_kind("dump") == "bagit_unpacked"
