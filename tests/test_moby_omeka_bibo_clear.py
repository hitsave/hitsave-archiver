"""Moby bibo:uri helpers for catalog corrections."""
from __future__ import annotations

from moby_omeka_fields import public_bibo_uri_rows_without_moby


def test_public_bibo_uri_rows_without_moby_strips_moby_keeps_other():
    item = {
        "bibo:uri": [
            {"@id": "https://www.mobygames.com/game/12345"},
            {"@id": "https://archive.hitsave.org/ark:/12345"},
            {"@id": "s3://hitsave-press-material/press-material/B/foo/"},
        ]
    }
    rows = public_bibo_uri_rows_without_moby(item)
    ids = [r["@id"] for r in rows]
    assert "https://www.mobygames.com/game/12345" not in ids
    assert "https://archive.hitsave.org/ark:/12345" in ids
    assert "s3://hitsave-press-material/press-material/B/foo/" not in ids
