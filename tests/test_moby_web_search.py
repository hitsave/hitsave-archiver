#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from moby_web_search import moby_game_id_from_url  # noqa: E402


def test_moby_game_id_from_url() -> None:
    assert moby_game_id_from_url("https://www.mobygames.com/game/70724/tekken-card-tournament/") == 70724
    assert moby_game_id_from_url("https://example.com/") is None
