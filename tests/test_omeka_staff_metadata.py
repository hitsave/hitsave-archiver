#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from omeka_staff_metadata import (  # noqa: E402
    aip_s3_uri,
    filter_bibo_uri_public,
    merge_staff_preservation_uri,
    merge_uri_property,
    uri_value_row,
)


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


def test_filter_bibo_uri_public_removes_s3() -> None:
    item = {
        "bibo:uri": [
            {"@id": "https://www.mobygames.com/game/1"},
            {"@id": "s3://hitsave-incoming/press-material/x/x/"},
        ]
    }
    out = filter_bibo_uri_public(item)
    assert out is not None
    assert len(out["bibo:uri"]) == 1
    assert out["bibo:uri"][0]["@id"].startswith("https://")


def test_merge_staff_preservation_uri_replaces_s3() -> None:
    term = "dcterms:relation"
    item = {
        term: [
            {"@id": "https://example.org/link"},
            {"@id": "s3://hitsave-incoming/press-material/a/a/"},
            {"@id": "s3://hitsave-incoming/press-material/b/b/"},
        ]
    }
    row = uri_value_row(1, "s3://hitsave-incoming/press-material/c/c/", is_public=False)
    out = merge_staff_preservation_uri(item, term, row)
    ids = [r["@id"] for r in out[term]]
    assert ids == ["https://example.org/link", "s3://hitsave-incoming/press-material/c/c/"]
    assert out[term][-1]["is_public"] is False


def test_merge_uri_property_idempotent() -> None:
    term = "dcterms:relation"
    uri = "s3://hitsave-incoming/press-material/x/"
    row = uri_value_row(99, uri, is_public=False)
    item = {term: [{"type": "uri", "@id": uri}]}
    assert merge_uri_property(item, term, row) is None
