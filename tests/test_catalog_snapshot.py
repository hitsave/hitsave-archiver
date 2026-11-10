"""Catalog snapshots: a dated copy of each package's Omeka record inside its bag (metadata/catalog/)."""
from __future__ import annotations

import hashlib
import json

import catalog_snapshot as cs

NAMES = {
    "item_sets": {1459: "Promotional Discs", 572: "Nintendo Switch"},
    "templates": {4: "Dump"},
    "classes": {27: "dctype:Software"},
}


def item(**kw) -> dict:
    base = {
        "@context": "https://archive.hitsave.org/api-context",
        "@id": "https://archive.hitsave.org/api/items/31466",
        "o:id": 31466,
        "o:is_public": True,
        "o:title": "FFVII AC Trailer",
        "o:created": {"@value": "2026-11-08T21:31:07+00:00"},
        "o:modified": {"@value": "2026-11-09T15:56:43+00:00"},
        "o:resource_class": {"o:id": 27},
        "o:resource_template": {"o:id": 4},
        "o:item_set": [{"o:id": 1459}],
        "o:site": [{"o:id": 2}],
        "o:media": [{"o:id": 31467}, {"o:id": 31468}],
        "thumbnail_display_urls": {"large": "https://x/large.jpg"},
        "dcterms:title": [{"type": "literal", "property_id": 1, "property_label": "Title",
                           "is_public": True, "@value": "FFVII AC Trailer"}],
        "dcterms:provenance": [{"type": "literal", "property_id": 51, "is_public": True,
                                "@value": "Anthony Perkins"}],
        "dcterms:rights": [{"type": "uri", "property_id": 15, "is_public": True,
                            "@id": "http://rightsstatements.org/vocab/InC/1.0/", "o:label": "In Copyright"}],
        "bibo:uri": [{"type": "uri", "property_id": 121, "is_public": False,
                      "@id": "https://s3.example/hitsave-dumps/x/", "o:label": "https://s3.example/hitsave-dumps/x/"}],
        "dcterms:publisher": [{"type": "resource:item", "property_id": 5, "is_public": True,
                               "@id": "https://archive.hitsave.org/api/items/1116", "value_resource_id": 1116,
                               "value_resource_name": "items", "url": None, "display_title": "Super Rare Games",
                               "thumbnail_url": "https://x/t.jpg", "thumbnail_title": "logo", "thumbnail_type": "image/png"}],
        "dcterms:alternative": [{"type": "literal", "property_id": 17, "is_public": True,
                                 "@value": "ファイナルファンタジー", "@language": "ja"}],
    }
    base.update(kw)
    return base


def snap(it=None, **kw) -> dict:
    return cs.build_snapshot(it or item(), package_id=kw.pop("package_id", "ffvii-ac-trailer"),
                             taken_at=kw.pop("taken_at", "2026-11-10T12:00:00Z"), names=NAMES)


def test_snapshot_identifies_the_record_and_explains_itself() -> None:
    s = snap()
    assert s["format"] == cs.SNAPSHOT_FORMAT
    assert "metadata/catalog/" in s["about"]
    assert s["package_id"] == "ffvii-ac-trailer"
    assert s["taken_at"] == "2026-11-10T12:00:00Z"
    assert s["omeka_item_id"] == 31466
    assert s["ark"] == "ark:/78322/31466"
    assert s["api_url"] == "https://archive.hitsave.org/api/items/31466"
    assert s["public"] is True
    assert s["created"] == "2026-11-08T21:31:07+00:00"
    assert s["omeka_modified"] == "2026-11-09T15:56:43+00:00"


def test_snapshot_names_item_sets_template_and_class() -> None:
    s = snap()
    assert s["item_sets"] == [{"id": 1459, "title": "Promotional Discs"}]
    assert s["resource_template"] == {"id": 4, "label": "Dump"}
    assert s["resource_class"] == {"id": 27, "term": "dctype:Software"}
    assert s["sites"] == [2]
    assert s["media"] == [31467, 31468]


def test_snapshot_without_template_or_class() -> None:
    s = snap(item(**{"o:resource_template": None, "o:resource_class": None, "o:item_set": []}))
    assert s["resource_template"] is None and s["resource_class"] is None and s["item_sets"] == []


def test_values_keep_meaning_and_drop_display_noise() -> None:
    v = snap()["values"]
    assert v["dcterms:title"] == [{"type": "literal", "value": "FFVII AC Trailer", "public": True}]
    assert v["dcterms:provenance"] == [{"type": "literal", "value": "Anthony Perkins", "public": True}]
    assert v["dcterms:rights"] == [{"type": "uri", "uri": "http://rightsstatements.org/vocab/InC/1.0/",
                                    "label": "In Copyright", "public": True}]
    assert v["bibo:uri"][0]["public"] is False
    assert v["dcterms:publisher"] == [{"type": "resource:item", "item_id": 1116, "ark": "ark:/78322/1116",
                                       "title": "Super Rare Games", "public": True}]
    assert v["dcterms:alternative"] == [{"type": "literal", "value": "ファイナルファンタジー", "language": "ja",
                                         "public": True}]
    assert "thumbnail_display_urls" not in json.dumps(snap())


def test_digest_ignores_when_the_copy_was_taken() -> None:
    a = snap(taken_at="2026-11-10T12:00:00Z")
    b = snap(item(**{"o:modified": {"@value": "2026-11-11T00:00:00+00:00"}}), taken_at="2026-11-11T12:00:00Z")
    assert cs.content_digest(a) == cs.content_digest(b)


def test_digest_changes_with_any_catalog_value() -> None:
    base = cs.content_digest(snap())
    assert cs.content_digest(snap(item(**{"o:is_public": False}))) != base
    assert cs.content_digest(snap(item(**{"dcterms:provenance": []}))) != base
    assert cs.content_digest(snap(item(**{"o:item_set": [{"o:id": 572}]}))) != base


def test_changed_fields_lists_what_moved() -> None:
    old = snap()
    new = snap(item(**{"o:is_public": False, "dcterms:provenance": [], "o:item_set": [{"o:id": 572}]}))
    assert cs.changed_fields(old, new) == ["dcterms:provenance", "item_sets", "public"]
    assert cs.changed_fields(old, snap(taken_at="2027-01-01T00:00:00Z")) == []


def test_snapshot_relpath_sorts_by_time() -> None:
    assert cs.snapshot_relpath("2026-11-10T12:00:00Z") == "metadata/catalog/catalog-20261110T120000Z.json"
    assert cs.snapshot_relpath("2026-11-09T23:59:59Z") < cs.snapshot_relpath("2026-11-10T00:00:00Z")


def test_latest_snapshot_from_tagmanifest_with_either_separator() -> None:
    tm = (
        "aa bag-info.txt\n"
        "bb  metadata/catalog/catalog-20261110T120000Z.json\n"
        "cc metadata/catalog/catalog-20261201T080000Z.json\n"
        "dd  metadata/premis/catalog-snapshot-20261201T080000Z-1234abcd.xml\n"
    )
    assert cs.latest_snapshot_relpath(tm) == "metadata/catalog/catalog-20261201T080000Z.json"
    assert cs.latest_snapshot_relpath("aa bag-info.txt\n") is None


def _tm_paths(tm: str) -> dict[str, str]:
    return {line.split(None, 1)[1]: line.split(None, 1)[0] for line in tm.splitlines() if line.strip()}


def test_first_snapshot_adds_file_event_and_tagmanifest_lines() -> None:
    s = snap()
    files, tm = cs.stage_snapshot("aa  bag-info.txt\n", s, previous=None)
    rel = "metadata/catalog/catalog-20261110T120000Z.json"
    assert list(files)[0] == rel
    assert json.loads(files[rel]) == s
    events = [p for p in files if p.startswith("metadata/premis/catalog-snapshot-")]
    assert len(events) == 1
    event = files[events[0]].decode()
    assert "metadata modification" in event
    assert "first copy" in event and rel in event
    paths = _tm_paths(tm)
    assert paths["bag-info.txt"] == "aa"
    for p, body in files.items():
        assert paths[p] == hashlib.sha256(body).hexdigest()


def test_later_snapshot_event_names_changed_fields_and_keeps_old_copy() -> None:
    old = snap()
    new = snap(item(**{"dcterms:provenance": []}), taken_at="2026-12-01T08:00:00Z")
    tm_before = "aa  bag-info.txt\nbb  metadata/catalog/catalog-20261110T120000Z.json\n"
    files, tm = cs.stage_snapshot(tm_before, new, previous=old)
    event = next(body.decode() for p, body in files.items() if p.startswith("metadata/premis/"))
    assert "dcterms:provenance" in event and "catalog-20261110T120000Z.json" in event
    paths = _tm_paths(tm)
    assert paths["metadata/catalog/catalog-20261110T120000Z.json"] == "bb"
    assert "metadata/catalog/catalog-20261201T080000Z.json" in paths


def test_snapshot_json_is_stable_text() -> None:
    a = cs.snapshot_bytes(snap())
    assert a == cs.snapshot_bytes(snap())
    assert a.endswith(b"\n") and "ファイナルファンタジー".encode() in a
