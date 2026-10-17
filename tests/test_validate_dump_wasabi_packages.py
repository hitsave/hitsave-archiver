#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from validate_dump_wasabi_packages import has_disc_payload, validate_keys  # noqa: E402

PREFIX = "dumps/DVD-Video/Coverdiscs/F/pkg-id"


def _keys(*rels: str) -> list[str]:
    root = f"{PREFIX}/"
    return [root + r for r in rels]


def test_validate_dvd_iso_passes() -> None:
    keys = _keys(
        "bagit.txt",
        "manifest-sha256.txt",
        "data/sip/disc.iso",
    )
    ok, detail = validate_keys(keys, prefix=PREFIX)
    assert ok is True
    assert "disc payload" in detail


def test_validate_audio_cd_bin_passes() -> None:
    keys = _keys(
        "bagit.txt",
        "manifest-sha256.txt",
        "data/redump-fwd0406-2/Audio CD (Track 01).bin",
        "data/redump-fwd0406-2/Audio CD.cue",
    )
    ok, _detail = validate_keys(keys, prefix=PREFIX)
    assert ok is True


def test_validate_bag_only_fails() -> None:
    keys = _keys("bagit.txt", "manifest-sha256.txt", "metadata/submission.json")
    ok, detail = validate_keys(keys, prefix=PREFIX)
    assert ok is False
    assert "disc image" in detail


def test_has_disc_payload_ignores_non_data_paths() -> None:
    assert has_disc_payload(["bagit.txt", "disc.iso"]) is False
    assert has_disc_payload(["data/pkg/track.bin"]) is True
