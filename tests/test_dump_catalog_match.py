"""Holding a new dump for review when the catalog already describes the same Redump disc."""
from __future__ import annotations

import dump_catalog_match as m

BASE = "https://omeka.example/api"
CREDS = {"key_identity": "i", "key_credential": "c"}


def relation_rows(*urls: str) -> dict:
    return {"dcterms:relation": [{"property_id": 13, "type": "uri", "@id": u} for u in urls]}


class FakeApi:
    @staticmethod
    def api_url(base, path, creds):
        return f"{base}/{path}?key"


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class FakeSession:
    """Answers items?property[0][text]=… with every item whose values contain the text."""

    def __init__(self, items):
        self.items = items
        self.queries: list[str] = []

    def get(self, url, params=None, timeout=None):
        text = params["property[0][text]"]
        self.queries.append(text)
        page = int(params.get("page", 1))
        hits = [i for i in self.items if text in str(i)]
        return FakeResponse(hits if page == 1 else [])


def item(item_id: int, title: str, *values: str) -> dict:
    return {"o:id": item_id, "o:title": title,
            "dcterms:description": [{"type": "literal", "@value": v} for v in values]}


HAND_MADE = item(16396, "Square Enix: Final Fantasy VII Advent Children Trailer",
                 "Disc included as part of a press kit", "http://redump.info/disc/58251/")


def test_disc_id_from_the_redump_relation():
    values = relation_rows("https://example.org/platform/pc", "https://redump.info/disc/58251")
    assert m.redump_disc_id(values) == "58251"
    assert m.redump_disc_id(relation_rows("https://example.org/platform/pc")) is None
    assert m.redump_disc_id({}) is None


def test_finds_hand_made_record_linking_the_disc():
    session = FakeSession([HAND_MADE])
    assert m.find_catalog_matches(session, BASE, CREDS, "58251", api=FakeApi) == [
        (16396, "Square Enix: Final Fantasy VII Advent Children Trailer")
    ]
    assert session.queries == ["redump.info/disc/58251", "redump.org/disc/58251"]


def test_longer_disc_number_is_not_a_match():
    session = FakeSession([item(1, "Other disc", "https://redump.info/disc/582510")])
    assert m.find_catalog_matches(session, BASE, CREDS, "58251", api=FakeApi) == []


def test_redump_org_link_matches_and_items_are_listed_once():
    both = item(7, "Old record", "http://redump.org/disc/58251/", "https://redump.info/disc/58251")
    session = FakeSession([both])
    assert m.find_catalog_matches(session, BASE, CREDS, "58251", api=FakeApi) == [(7, "Old record")]


def test_matches_for_new_item_checks_only_new_items():
    session = FakeSession([HAND_MADE])
    values = relation_rows("https://redump.info/disc/58251")
    assert m.matches_for_new_item(session, BASE, CREDS, values, existing_item_id=None, api=FakeApi, env={}) == [
        (16396, "Square Enix: Final Fantasy VII Advent Children Trailer")
    ]
    # Re-applying to the dump's own item never holds it.
    assert m.matches_for_new_item(session, BASE, CREDS, values, existing_item_id=31466, api=FakeApi, env={}) == []
    # No Redump disc page, nothing to compare.
    assert m.matches_for_new_item(session, BASE, CREDS, {}, existing_item_id=None, api=FakeApi, env={}) == []


def test_override_creates_the_item_anyway():
    session = FakeSession([HAND_MADE])
    values = relation_rows("https://redump.info/disc/58251")
    env = {m.ALLOW_ENV: "1"}
    assert m.matches_for_new_item(session, BASE, CREDS, values, existing_item_id=None, api=FakeApi, env=env) == []
    assert session.queries == []


def test_hold_note_names_items_and_how_to_release():
    note = m.hold_note("58251", [(16396, "Square Enix: Final Fantasy VII Advent Children Trailer")])
    assert note.startswith("Not registered in Omeka: item 16396 (Square Enix: Final Fantasy VII Advent "
                           "Children Trailer) already describes redump.info disc 58251.")
    assert m.ALLOW_ENV in note
