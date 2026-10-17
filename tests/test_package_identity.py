#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from package_identity import (  # noqa: E402
    canonical_sip_line,
    make_package_id,
    parse_package_id,
    sip_fingerprint_sha256,
    sort_letter,
    title_slug,
    title_slug_legacy_no_transliteration,
)


def test_title_slug_and_sort_letter() -> None:
    assert title_slug("Tekken 7") == "tekken-7"
    assert sort_letter("Tekken 7") == "T"
    assert sort_letter("007 Agent") == "0-9"


def test_title_slug_transliterates_macron_romanization() -> None:
    title = "Famitsu DVD 2002 E3 Tokuh\u014d saishin g\u0113mu eiz\u014d katarogu"
    assert title_slug_legacy_no_transliteration(title) == (
        "famitsu-dvd-2002-e3-tokuh-saishin-g-mu-eiz-katarogu"
    )
    assert title_slug(title) == "famitsu-dvd-2002-e3-tokuho-saishin-gemu-eizo-katarogu"


def test_sip_fingerprint_stable() -> None:
    line = canonical_sip_line(
        "hitsave-submission:press-material-community-2020-07-23",
        "Representation_Copies/T/Tekken_7",
    )
    a = sip_fingerprint_sha256(
        "hitsave-submission:press-material-community-2020-07-23",
        "Representation_Copies/T/Tekken_7",
    )
    b = sip_fingerprint_sha256(
        "hitsave-submission:press-material-community-2020-07-23",
        "Representation_Copies/T/Tekken_7/",
    )
    assert len(a) == 64
    assert a == b
    assert "press-material" in line


def test_package_id_roundtrip() -> None:
    uid = "01947c2a-8b3d-7a1e-b2c4-9d8e1f2a3b4c"
    pid, parsed_uid = make_package_id("Tekken 7", uid)
    assert parsed_uid == uid
    assert pid == f"tekken-7-{uid}"
    slug, again = parse_package_id(pid)
    assert slug == "tekken-7"
    assert again == uid
