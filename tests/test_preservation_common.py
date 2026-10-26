#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from preservation_common import should_skip  # noqa: E402


def test_should_skip_ocr_crop_debug_tree() -> None:
    kept = Path("/game/1.0.6/image.xci")
    debug = Path("/game/1.0.6/_ocr_crop_debug/crop.png")
    assert not should_skip(kept)
    assert should_skip(debug)
