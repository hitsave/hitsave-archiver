"""resolve_game_config picks up HITSAVE_BATCH_KEY for batch output layout."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from preservation_common import resolve_game_config  # noqa: E402


def test_batch_key_from_env(monkeypatch) -> None:
    monkeypatch.setenv("HITSAVE_BATCH_KEY", "my-run")
    cfg = resolve_game_config(
        {
            "ingest_kind": "press_material",
            "source_subpath": "Example/Game",
            "omeka_item_title": "Example Game",
            "game_key": "example-game-00000000-0000-7000-8000-000000000001",
        },
        agent_version="test",
    )
    assert cfg["batch_key"] == "my-run"
    assert "/batch/my-run/" in cfg["bagit_bag_dir"]
