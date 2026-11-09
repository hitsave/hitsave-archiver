"""Rights statement (dcterms:rights, RightsStatements.org) and access (dcterms:accessRights) on items."""
from __future__ import annotations

import rights_access as ra

RIGHTS_PID, ACCESS_PID = 15, 99


def lit(pid, text):
    return {"property_id": pid, "type": "literal", "@value": text}


MOBY = lit(RIGHTS_PID, "Release date and credits from MobyGames.")
EDU = {"property_id": RIGHTS_PID, "type": "uri", "@id": "http://rightsstatements.org/vocab/InC-EDU/1.0/",
       "o:label": "In Copyright - Educational Use Permitted"}


def test_default_is_in_copyright_uri() -> None:
    rows = ra.merged_rights(None, None, RIGHTS_PID)
    assert rows == [{"property_id": RIGHTS_PID, "type": "uri", "@id": "http://rightsstatements.org/vocab/InC/1.0/",
                     "o:label": "In Copyright"}]


def test_existing_credit_kept_and_inc_added() -> None:
    rows = ra.merged_rights({"dcterms:rights": [MOBY]}, None, RIGHTS_PID)
    assert rows[0]["@value"] == MOBY["@value"] and rows[1]["@id"].endswith("/InC/1.0/")


def test_hand_set_statement_is_never_replaced() -> None:
    rows = ra.merged_rights({"dcterms:rights": [MOBY, EDU]}, None, RIGHTS_PID)
    assert [r.get("@id") for r in rows if r.get("@id")] == [EDU["@id"]]


def test_new_credit_replaces_old_credit_but_keeps_the_statement() -> None:
    new_credit = [lit(RIGHTS_PID, "Credits from MobyGames (updated).")]
    rows = ra.merged_rights({"dcterms:rights": [MOBY, EDU]}, new_credit, RIGHTS_PID)
    assert [r.get("@value") or r.get("@id") for r in rows] == ["Credits from MobyGames (updated).", EDU["@id"]]


def test_statement_detection_accepts_https_and_trailing_variants() -> None:
    assert ra.is_rights_statement({"@id": "https://rightsstatements.org/vocab/InC-RUU/1.0/"})
    assert ra.is_rights_statement({"@id": "http://rightsstatements.org/page/InC/1.0/?language=en"})
    assert not ra.is_rights_statement({"@value": "rightsstatements.org"})


def test_access_labels() -> None:
    assert ra.access_label("press_material") == "Public"
    assert ra.access_label("dump") == "Not public, request access if needed"
    assert ra.access_label("dump", disc_contents_public=True) == "Disc contents public"


def test_access_kept_unless_forced() -> None:
    item = {"dcterms:accessRights": [lit(ACCESS_PID, "Public")]}
    assert ra.merged_access(item, "Not public, request access if needed", ACCESS_PID) == [lit(ACCESS_PID, "Public")]
    assert ra.merged_access(item, "Not public, request access if needed", ACCESS_PID, force=True) == [
        lit(ACCESS_PID, "Not public, request access if needed")]
    assert ra.merged_access(None, "Public", ACCESS_PID) == [lit(ACCESS_PID, "Public")]


def test_values_for_item_sets_both_fields() -> None:
    values = ra.values_for_item(None, kind="dump", rights_pid=RIGHTS_PID, access_pid=ACCESS_PID)
    assert values["dcterms:rights"][0]["@id"].endswith("/InC/1.0/")
    assert values["dcterms:accessRights"] == [lit(ACCESS_PID, "Not public, request access if needed")]


class _Api:
    @staticmethod
    def resolve_property_id(session, base, creds, term):
        return {"dcterms:provenance": 51, "dcterms:rights": RIGHTS_PID, "dcterms:accessRights": ACCESS_PID}[term]

    literal_value = staticmethod(lit)


def test_dump_catalog_values_get_inc_and_not_public(monkeypatch) -> None:
    import dump_catalog_omeka as dco
    monkeypatch.setattr(dco, "_build_dump_catalog_values", lambda *a, **k: ({"dcterms:title": ["t"]}, "T"))
    values, _ = dco.build_dump_catalog_values(None, "b", {}, {}, storage_uri_base="", api=_Api, existing_item=None)
    assert values["dcterms:rights"][0]["@id"] == ra.IN_COPYRIGHT_URI
    assert values["dcterms:accessRights"][0]["@value"] == ra.NOT_PUBLIC
    existing = {"dcterms:rights": [EDU], "dcterms:accessRights": [lit(ACCESS_PID, ra.CONTENTS_PUBLIC)]}
    again, _ = dco.build_dump_catalog_values(None, "b", {}, {}, storage_uri_base="", api=_Api, existing_item=existing)
    assert again["dcterms:rights"] == [EDU]
    assert again["dcterms:accessRights"][0]["@value"] == ra.CONTENTS_PUBLIC


def test_moby_credit_does_not_wipe_the_rights_statement() -> None:
    import moby_omeka_fields as mof
    payload: dict = {}
    item = {"dcterms:rights": [MOBY, EDU]}
    mof.append_moby_fields_to_payload(
        payload, {"moby_game_id": 1, "moby_title": "Tekken", "moby_catalog_fields": []},
        {"attribution_suffix": "Data by MobyGames.com",
         "public_game_url_template": "https://www.mobygames.com/game/{game_id}"},
        session=None, base="b", creds={}, literal_value=lit, uri_value=lambda pid, v: {"@id": v},
        resolve_property_id=lambda s, b, c, term: {"dcterms:rights": RIGHTS_PID}.get(term, 1),
        item_body=item,
    )
    rights = payload["dcterms:rights"]
    assert EDU in rights and sum(1 for r in rights if ra.is_rights_statement(r)) == 1
    assert not any(r.get("@value") == MOBY["@value"] for r in rights)  # old credit replaced


def test_apply_to_payload_for_press_registration() -> None:
    payload = {"dcterms:rights": [lit(RIGHTS_PID, "credit")]}
    ra.apply_to_payload(payload, None, kind="press_material",
                        resolve_pid=lambda term: {"dcterms:rights": RIGHTS_PID, "dcterms:accessRights": ACCESS_PID}[term])
    assert [r.get("@value") or r.get("@id") for r in payload["dcterms:rights"]] == ["credit", ra.IN_COPYRIGHT_URI]
    assert payload["dcterms:accessRights"][0]["@value"] == ra.PUBLIC


def test_disc_contents_decision_forces_access() -> None:
    item = {"dcterms:accessRights": [lit(ACCESS_PID, ra.NOT_PUBLIC)], "dcterms:rights": [MOBY]}
    upd = ra.disc_contents_access_update(item, display_public=True, access_pid=ACCESS_PID)
    assert upd == {"dcterms:accessRights": [lit(ACCESS_PID, ra.CONTENTS_PUBLIC)]}
    upd = ra.disc_contents_access_update(item, display_public=False, access_pid=ACCESS_PID)
    assert upd == {}  # already right: nothing to write
