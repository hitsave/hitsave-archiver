"""Tombstones (items with dcterms:isReplacedBy) leave the site so browse and search skip them."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("ht", ROOT / "scripts" / "hide-tombstones.py")
ht = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ht)


def item(**kw):
    base = {"@context": "c", "@id": "https://a/api/items/26725", "@type": "o:Item", "o:id": 26725,
            "o:is_public": True, "o:site": [{"@id": "https://a/api/sites/2", "o:id": 2}], "o:item_set": [],
            "dcterms:title": [{"type": "literal", "property_id": 1, "@value": "T"}],
            "dcterms:isReplacedBy": [{"type": "resource", "property_id": 29, "value_resource_id": 631}]}
    base.update(kw)
    return base


def test_tombstone_detection() -> None:
    assert ht.is_tombstone(item())
    assert not ht.is_tombstone(item(**{"dcterms:isReplacedBy": []}))


def test_body_drops_sites_and_keeps_everything_else() -> None:
    body = ht.body_without_sites(item())
    assert body["o:site"] == []
    assert "@context" not in body and "@id" not in body
    assert body["dcterms:isReplacedBy"] == item()["dcterms:isReplacedBy"]
    assert body["o:is_public"] is True


def test_needs_change_only_while_on_a_site() -> None:
    assert ht.needs_change(item())
    assert not ht.needs_change(item(**{"o:site": []}))
    assert not ht.needs_change(item(**{"dcterms:isReplacedBy": []}))


def test_verify_allows_only_site_and_modified() -> None:
    before = item()
    after = item(**{"o:site": [], "o:modified": {"@value": "now"}})
    assert ht.unexpected_changes(before, after) == []
    after["o:is_public"] = False
    assert ht.unexpected_changes(before, after) == ["o:is_public"]
