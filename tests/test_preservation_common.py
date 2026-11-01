#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from preservation_common import legacy_skip_reason, should_skip  # noqa: E402


def test_everything_is_kept_including_ocr_debug_and_dotfiles() -> None:
    for p in ("/game/1.0.6/image.xci", "/game/1.0.6/_ocr_crop_debug/crop.png", "/kit/.checksum.md5"):
        assert not should_skip(Path(p))
    # The old policy is still recognised (for packages preserved before 2026-11-01).
    assert legacy_skip_reason(Path("/game/1.0.6/_ocr_crop_debug/crop.png")) == "ocr debug output"
