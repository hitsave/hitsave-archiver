#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from omeka_staff_metadata import aip_s3_uri, merge_uri_property, uri_value_row  # noqa: E402


def test_aip_s3_uri() -> None:
    assert (
        aip_s3_uri(bucket="hitsave-incoming", storage_prefix="press-material/game-a/")
        == "s3://hitsave-incoming/press-material/game-a/"
    )


def test_merge_uri_property_appends() -> None:
    term = "dcterms:relation"
    row = uri_value_row(99, "s3://b/prefix/", is_public=False)
    item = {term: [{"type": "uri", "@id": "https://example.org/"}]}
    out = merge_uri_property(item, term, row)
    assert out is not None
    assert len(out[term]) == 2
    assert out[term][-1]["is_public"] is False


def test_merge_uri_property_idempotent() -> None:
    term = "dcterms:relation"
    uri = "s3://hitsave-incoming/press-material/x/"
    row = uri_value_row(99, uri, is_public=False)
    item = {term: [{"type": "uri", "@id": uri}]}
    assert merge_uri_property(item, term, row) is None
