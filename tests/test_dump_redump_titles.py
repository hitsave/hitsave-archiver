#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from hitsave_dump_sidecars.redump import RedumpSubmission, pick_omeka_title_from_submission  # noqa: E402
from yaml_config import dumps_json  # noqa: E402


def test_pick_title_prefers_japanese_foreign_over_macron_roman() -> None:
    sub = RedumpSubmission(
        fields={
            "Title": "Famitsu DVD 2002 E3 Tokuh\u014d saishin g\u0113mu eiz\u014d katarogu",
            "Foreign Title (Non-latin)": "ファミ通DVD 2002 E3 特報 最新ゲーム映像カタログ",
        }
    )
    assert pick_omeka_title_from_submission(sub) == "ファミ通DVD 2002 E3 特報 最新ゲーム映像カタログ"


def test_pick_title_keeps_ascii_english_when_foreign_is_japanese() -> None:
    sub = RedumpSubmission(
        fields={
            "Title": "Famitsu Wave DVD Presents",
            "Foreign Title (Non-latin)": "ファミ通WaveDVD",
        }
    )
    assert pick_omeka_title_from_submission(sub) == "Famitsu Wave DVD Presents"


def test_dumps_json_writes_utf8_literals() -> None:
    payload = {"title": "Tokuh\u014d"}
    text = dumps_json(payload, indent=2)
    assert "\\u014d" not in text
    assert "Tokuhō" in text or "ō" in text
