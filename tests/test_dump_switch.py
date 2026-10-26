#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from dump_switch import (  # noqa: E402
    parse_switch_submission_xml,
    switch_submission_valid,
    switch_catalog_sha256,
)

FIXTURE = ROOT / "fixtures" / "switch-submission-blud.xml"


def test_parse_switch_submission_xml() -> None:
    text = FIXTURE.read_text(encoding="utf-8")
    sub = parse_switch_submission_xml(text)
    assert sub.game_name == "#BLUD"
    assert sub.gameid1 == "010060201E0A4000"
    assert sub.region == "Europe"
    assert sub.project == "No-Intro"
    assert switch_submission_valid(sub)
    primary = sub.primary_cart_image()
    assert primary is not None
    assert primary.extension == "xci"
    assert primary.fmt == "Default"
    assert switch_catalog_sha256(sub) == (
        "ae29a7548af81f0df4035013af1ab2ec9912ca1cf0d4ce34e03d7fbeb5a823cd"
    )
