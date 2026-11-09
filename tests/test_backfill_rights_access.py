"""One-time backfill: In Copyright + access level on existing items."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import rights_access as ra

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("bra", ROOT / "scripts" / "backfill-rights-access.py")
bra = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bra)

PIDS = {"dcterms:rights": 15, "dcterms:accessRights": 99}


def lit(pid, text):
    return {"type": "literal", "property_id": pid, "@value": text, "is_public": True}


def item(**kw):
    base = {"@context": "x", "@id": "https://a/api/items/1", "o:id": 1, "o:title": "T", "o:is_public": True,
            "o:media": [], "dcterms:title": [lit(1, "T")]}
    base.update(kw)
    return base


def test_access_for_ledger_kinds_and_unknown_items() -> None:
    assert bra.access_for(item(), {"ingest_kind": "press_material"}) == ra.PUBLIC
    assert bra.access_for(item(), {"ingest_kind": "dump", "disc_contents_display": "review"}) == ra.NOT_PUBLIC
    assert bra.access_for(item(), {"ingest_kind": "dump", "disc_contents_display": "public"}) == ra.CONTENTS_PUBLIC
    assert bra.access_for(item(**{"o:media": [{"o:id": 5}]}), None) is None  # no ledger row: left for staff
    assert bra.access_for(item(), None) is None


def test_body_adds_inc_and_access_and_keeps_everything_else() -> None:
    before = item(**{"dcterms:rights": [lit(15, "Data by MobyGames.com")]})
    body, changed = bra.updated_body(before, {"ingest_kind": "press_material"}, PIDS)
    assert changed == ["dcterms:accessRights", "dcterms:rights"]
    assert "@context" not in body and "@id" not in body
    assert body["dcterms:title"] == before["dcterms:title"]
    assert [r.get("@value") or r.get("@id") for r in body["dcterms:rights"]] == ["Data by MobyGames.com", ra.IN_COPYRIGHT_URI]
    assert body["dcterms:accessRights"][0]["@value"] == ra.PUBLIC


def test_item_with_statement_and_access_is_unchanged() -> None:
    edu = {"type": "uri", "property_id": 15, "@id": "http://rightsstatements.org/vocab/InC-EDU/1.0/"}
    before = item(**{"dcterms:rights": [edu], "dcterms:accessRights": [lit(99, "Public")]})
    _, changed = bra.updated_body(before, {"ingest_kind": "press_material"}, PIDS)
    assert changed == []


def test_unknown_item_gets_rights_only() -> None:
    body, changed = bra.updated_body(item(), None, PIDS)
    assert changed == ["dcterms:rights"] and "dcterms:accessRights" not in body


def test_verify_flags_any_other_change() -> None:
    before = item()
    after = item(**{"dcterms:rights": [ra.in_copyright_value(15)], "o:modified": "now"})
    assert bra.unexpected_changes(before, after) == []
    after["dcterms:title"] = [lit(1, "Changed")]
    assert bra.unexpected_changes(before, after) == ["dcterms:title"]
