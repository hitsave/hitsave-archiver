#!/usr/bin/env python3
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from dump_classify import classify_dump_sip  # noqa: E402

REDUMP_SIDECAR_FIXTURE = ROOT / "fixtures" / "redump-submissionInfo-fwd0307.txt"


def test_redump_sidecar_classifies_system_and_category() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        sip = Path(tmp)
        assert REDUMP_SIDECAR_FIXTURE.is_file(), f"missing fixture {REDUMP_SIDECAR_FIXTURE}"
        shutil.copy(REDUMP_SIDECAR_FIXTURE, sip / "!submissionInfo.txt")
        clf = classify_dump_sip(
            sip, package_id="famitsu-wave-dvd-01947c2a-test", sort_letter="F"
        )
        assert clf.dump_class == "redump"
        assert clf.dump_system == "DVD-Video"
        assert clf.dump_category == "Coverdiscs"
        assert clf.package_storage_prefix == (
            "dumps/DVD-Video/Coverdiscs/F/famitsu-wave-dvd-01947c2a-test"
        )


AUDIO_CD_SUBMISSION = """\
Common Disc Info:
\tTitle: Test Audio CD
\tSystem: Audio CD
\tCategory: Coverdiscs

Tracks and Write Offsets:

<rom name="track-a.bin" size="4" crc="364b8887" md5="ab356a337456ca17f73b4270b0f5220f" sha1="ebf1d1d4511bc11d7a9704c0a39b1d1c1c1c1c1c" />
"""


def test_audio_cd_classifies_as_redump() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        sip = Path(tmp)
        (sip / "!submissionInfo.txt").write_text(AUDIO_CD_SUBMISSION, encoding="utf-8")
        clf = classify_dump_sip(sip, package_id="redump-fwd0406-2", sort_letter="S")
        assert clf.dump_class == "redump"
        assert clf.dump_system == "Audio CD"
        assert clf.dump_category == "Coverdiscs"
        assert clf.package_storage_prefix == (
            "dumps/Audio CD/Coverdiscs/S/redump-fwd0406-2"
        )


def test_missing_sidecar_is_other() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        sip = Path(tmp)
        (sip / "game.iso").write_bytes(b"x")
        clf = classify_dump_sip(sip, package_id="pc-folder-01947c2a-test")
        assert clf.dump_class == "other"
        assert clf.dump_system is None
        assert clf.package_storage_prefix == "dumps/Others/X/pc-folder-01947c2a-test"
