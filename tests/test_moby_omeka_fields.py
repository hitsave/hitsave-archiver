#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from moby_omeka_fields import moby_bibo_uri_rows  # noqa: E402


def _uri_row(pid: int, url: str) -> dict:
    return {"property_id": pid, "type": "uri", "@id": url}


def test_moby_bibo_uri_rows_drops_s3_and_adds_moby() -> None:
    item = {
        "bibo:uri": [
            {"@id": "s3://hitsave-incoming/press-material/x/x.tar"},
            {"@id": "https://www.mobygames.com/game/99/"},
        ]
    }
    rows = moby_bibo_uri_rows(item, "https://www.mobygames.com/game/99/", 1, _uri_row)
    assert len(rows) == 1
    assert rows[0]["@id"].startswith("https://www.mobygames.com/")


def test_moby_bibo_uri_rows_adds_from_game_id_style_url() -> None:
    rows = moby_bibo_uri_rows(None, "https://www.mobygames.com/game/89713", 1, _uri_row)
    assert len(rows) == 1
    assert rows[0].get("is_public") is True
