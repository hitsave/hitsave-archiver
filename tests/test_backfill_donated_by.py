"""Moving "Donated by X." lines out of descriptions into dcterms:provenance."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("donated", ROOT / "scripts" / "backfill-donated-by-provenance.py")
donated = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(donated)

DESC = """Disc included as part of a press kit distributed by Square Enix at E3 2005.

Original disc files remain on record.

Donated by Anthony Perkins.

Further Reading:"""


def test_credit_line_moves_out_and_paragraphs_close_up() -> None:
    text, donors = donated.split_donor_lines(DESC)
    assert donors == ["Anthony Perkins"]
    assert text == """Disc included as part of a press kit distributed by Square Enix at E3 2005.

Original disc files remain on record.

Further Reading:"""


def test_line_forms_and_trailing_period() -> None:
    assert donated.split_donor_lines("Item donated by Brock of Nostalgist's Archive.") == ("", ["Brock of Nostalgist's Archive"])
    assert donated.split_donor_lines("A.\n\ndonated by: noigeaR") == ("A.", ["noigeaR"])
    assert donated.split_donor_lines("Donated by Acme Inc.")[1] == ["Acme Inc."]  # abbreviation keeps its dot


def test_prose_mentions_are_left_alone() -> None:
    prose = ("If you have an identical tape you would like to donate, please contact us.\n"
             "This item was donated by a collector in 2004 and restored.")
    assert donated.split_donor_lines(prose) == (prose, [])


def test_item_body_moves_credit_and_keeps_other_values() -> None:
    item = {
        "@id": "x", "o:id": 1, "o:title": "T",
        "dcterms:description": [
            {"type": "literal", "property_id": 4, "@value": DESC, "is_public": True},
            {"type": "uri", "property_id": 4, "@id": "http://redump.info/disc/58251/", "o:label": "Redump"},
        ],
    }
    body, donors = donated.updated_body(item)
    assert donors == ["Anthony Perkins"]
    assert "@id" not in body
    assert "Donated by" not in body["dcterms:description"][0]["@value"]
    assert body["dcterms:description"][1]["@id"] == "http://redump.info/disc/58251/"
    assert body["dcterms:provenance"] == [{"type": "literal", "property_id": 51, "@value": "Anthony Perkins", "is_public": True}]
    # Rerun on the updated item: nothing to do.
    again, none = donated.updated_body({**body, "dcterms:provenance": body["dcterms:provenance"]})
    assert none == []


def test_existing_provenance_is_not_duplicated_and_empty_description_dropped() -> None:
    item = {
        "dcterms:description": [{"type": "literal", "property_id": 4, "@value": "Donated by Andrew Czudak", "is_public": True}],
        "dcterms:provenance": [{"type": "literal", "property_id": 51, "@value": "Andrew Czudak", "is_public": True}],
    }
    body, donors = donated.updated_body(item)
    assert donors == ["Andrew Czudak"]
    assert "dcterms:description" not in body
    assert len(body["dcterms:provenance"]) == 1
