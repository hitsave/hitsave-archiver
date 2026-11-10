"""sync-catalog-snapshots.py: add a dated catalog copy to bags whose Omeka record changed."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path

import pytest

import catalog_snapshot as cs

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("scs", ROOT / "scripts" / "sync-catalog-snapshots.py")
scs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scs)


class NoSuchKey(Exception):
    pass


class FakeS3:
    def __init__(self, objects: dict[str, bytes]):
        self.objects = dict(objects)
        self.puts: list[str] = []
        self.on_put = None
        self.exceptions = type("E", (), {"NoSuchKey": NoSuchKey})

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise NoSuchKey(Key)
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, Bucket, Key, Body, **kw):
        self.puts.append(Key)
        self.objects[Key] = bytes(Body)
        if self.on_put:
            self.on_put(Key)


ROOT_PREFIX = "dumps/F/ffvii/"
TM = f"{ROOT_PREFIX}tagmanifest-sha256.txt"


def item(**kw):
    base = {"@id": "https://a/api/items/31466", "o:id": 31466, "o:is_public": True,
            "dcterms:title": [{"type": "literal", "property_id": 1, "is_public": True, "@value": "T"}]}
    base.update(kw)
    return base


def snap(it=None, taken_at="2026-11-10T12:00:00Z"):
    return cs.build_snapshot(it or item(), package_id="ffvii", taken_at=taken_at, names={})


def tm_paths(text: str) -> dict[str, str]:
    return {l.split(None, 1)[1]: l.split(None, 1)[0] for l in text.splitlines() if l.strip()}


def bag_with(snapshot=None) -> FakeS3:
    tm = "aa  bag-info.txt\n"
    objects = {}
    if snapshot is not None:
        rel = cs.snapshot_relpath(snapshot["taken_at"])
        body = cs.snapshot_bytes(snapshot)
        objects[ROOT_PREFIX + rel] = body
        tm += f"{hashlib.sha256(body).hexdigest()} {rel}\n"
    objects[TM] = tm.encode()
    return FakeS3(objects)


def test_bag_without_a_copy_gets_its_first_one_tagmanifest_last() -> None:
    s3 = bag_with()
    new = snap(taken_at="2026-11-11T05:30:00Z")
    result = scs.sync_bag(s3, bucket="b", root=ROOT_PREFIX, snapshot=new, execute=True)
    assert result["status"] == "new"
    rel = "metadata/catalog/catalog-20261111T053000Z.json"
    assert s3.puts[0] == ROOT_PREFIX + rel
    assert s3.puts[-1] == TM
    assert len(s3.puts) == 3  # snapshot, PREMIS event, tagmanifest
    paths = tm_paths(s3.objects[TM].decode())
    for p in (rel, next(k for k in paths if k.startswith("metadata/premis/catalog-snapshot-"))):
        assert paths[p] == hashlib.sha256(s3.objects[ROOT_PREFIX + p]).hexdigest()
    assert json.loads(s3.objects[ROOT_PREFIX + rel])["taken_at"] == "2026-11-11T05:30:00Z"


def test_unchanged_record_writes_nothing() -> None:
    s3 = bag_with(snap())
    result = scs.sync_bag(s3, bucket="b", root=ROOT_PREFIX, snapshot=snap(taken_at="2026-11-12T05:30:00Z"),
                          execute=True)
    assert result["status"] == "unchanged"
    assert s3.puts == []


def test_changed_record_adds_a_copy_and_keeps_the_old_one() -> None:
    old = snap()
    s3 = bag_with(old)
    new = snap(item(**{"o:is_public": False}), taken_at="2026-11-12T05:30:00Z")
    result = scs.sync_bag(s3, bucket="b", root=ROOT_PREFIX, snapshot=new, execute=True)
    assert result["status"] == "changed"
    assert result["changed"] == ["public"]
    assert result["previous"] == "metadata/catalog/catalog-20261110T120000Z.json"
    paths = tm_paths(s3.objects[TM].decode())
    assert "metadata/catalog/catalog-20261110T120000Z.json" in paths
    assert "metadata/catalog/catalog-20261112T053000Z.json" in paths


def test_dry_run_reports_without_writing() -> None:
    s3 = bag_with()
    result = scs.sync_bag(s3, bucket="b", root=ROOT_PREFIX, snapshot=snap(), execute=False)
    assert result["status"] == "new" and result["dry_run"] is True
    assert s3.puts == []


def test_tagmanifest_changed_by_someone_else_stops_before_overwriting_it() -> None:
    s3 = bag_with()

    def meddle(key):
        if key.endswith(".json"):
            s3.objects[TM] = b"aa  bag-info.txt\nbb  metadata/other.txt\n"

    s3.on_put = meddle
    with pytest.raises(RuntimeError, match="tagmanifest changed"):
        scs.sync_bag(s3, bucket="b", root=ROOT_PREFIX, snapshot=snap(), execute=True)
    assert TM not in s3.puts
    assert b"metadata/other.txt" in s3.objects[TM]


def test_listed_copy_that_does_not_match_its_digest_is_an_error() -> None:
    s3 = bag_with(snap())
    s3.objects[ROOT_PREFIX + "metadata/catalog/catalog-20261110T120000Z.json"] = b"{}\n"
    with pytest.raises(RuntimeError, match="does not match"):
        scs.sync_bag(s3, bucket="b", root=ROOT_PREFIX, snapshot=snap(), execute=True)


def test_names_are_fetched_once_per_id() -> None:
    calls = []

    def fetch(path):
        calls.append(path)
        return {"item_sets/7": {"o:title": "Set"}, "resource_templates/4": {"o:label": "Dump"},
                "resource_classes/27": {"o:term": "dctype:Software"}}[path]

    names = scs.Names(fetch)
    items = [item(**{"o:item_set": [{"o:id": 7}], "o:resource_template": {"o:id": 4},
                     "o:resource_class": {"o:id": 27}})] * 3
    out = names.for_items(items)
    assert out == {"item_sets": {7: "Set"}, "templates": {4: "Dump"}, "classes": {27: "dctype:Software"}}
    assert sorted(calls) == ["item_sets/7", "resource_classes/27", "resource_templates/4"]


def test_missing_name_is_none_not_a_crash() -> None:
    def fetch(path):
        raise scs.NotFound(path)

    out = scs.Names(fetch).for_items([item(**{"o:item_set": [{"o:id": 9}]})])
    assert out["item_sets"] == {9: None}


def test_items_are_paged_and_filtered_to_ledger_ids() -> None:
    pages = {1: [item(**{"o:id": 1}), item(**{"o:id": 2})], 2: [item(**{"o:id": 3})], 3: []}
    seen = []

    def fetch_page(page):
        seen.append(page)
        return pages[page]

    got = scs.ledger_items(fetch_page, {2, 3, 99})
    assert sorted(got) == [2, 3]
    assert seen == [1, 2, 3]
