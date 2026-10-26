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


PS_COVERDISC_SUBMISSION = """\
Common Disc Info:
\tTitle: Famitsu Wave Test
\tSystem: Sony PlayStation
\tCategory: Coverdiscs

Tracks and Write Offsets:
\tDAT:

<rom name="SLPM_80312 (Track 1).bin" size="4" crc="364b8887" md5="ab356a337456ca17f73b4270b0f5220f" sha1="ebf1d1d4511bc11d7a9704c0a39b1d1c1c1c1c1c" />
"""


def test_playstation_rom_track_classifies_as_redump() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        sip = Path(tmp)
        (sip / "!submissionInfo.txt").write_text(PS_COVERDISC_SUBMISSION, encoding="utf-8")
        clf = classify_dump_sip(sip, package_id="slpm-80312-test", sort_letter="F")
        assert clf.dump_class == "redump"
        assert clf.dump_system == "Sony PlayStation"
        assert clf.dump_category == "Coverdiscs"
        assert clf.package_storage_prefix == (
            "dumps/Sony PlayStation/Coverdiscs/F/slpm-80312-test"
        )


SWITCH_FIXTURE = ROOT / "fixtures" / "switch-submission-blud.xml"


def test_switch_submission_xml_classifies() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        sip = Path(tmp)
        assert SWITCH_FIXTURE.is_file()
        shutil.copy(
            SWITCH_FIXTURE,
            sip / "#BLUD - hitsaveorg - 2026-05-01 Submission.xml",
        )
        clf = classify_dump_sip(sip, package_id="blud-switch-test", sort_letter="B")
        assert clf.dump_class == "switch"
        assert clf.dump_system == "Nintendo Switch"
        assert clf.dump_category == "Games"
        assert clf.package_storage_prefix == "dumps/Nintendo Switch/B/blud-switch-test"


def test_switch_game_folder_layout_classifies() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        game = Path(tmp) / "Aka"
        version = game / "1.0.6"
        scans = game / "Scans"
        version.mkdir(parents=True)
        scans.mkdir()
        (scans / "cart.png").write_bytes(b"png")
        assert SWITCH_FIXTURE.is_file()
        shutil.copy(
            SWITCH_FIXTURE,
            version / "Aka - hitsaveorg - 2026-05-01 Submission.xml",
        )
        clf = classify_dump_sip(game, package_id="switch-aka-test", sort_letter="A")
        assert clf.dump_class == "switch"
        assert clf.switch_submission is not None
        assert clf.switch_submission.game_name == "#BLUD"


def test_missing_sidecar_is_other() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        sip = Path(tmp)
        (sip / "game.iso").write_bytes(b"x")
        clf = classify_dump_sip(sip, package_id="pc-folder-01947c2a-test")
        assert clf.dump_class == "other"
        assert clf.dump_system is None
        assert clf.package_storage_prefix == "dumps/Others/X/pc-folder-01947c2a-test"
