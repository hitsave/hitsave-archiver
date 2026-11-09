"""`donor` in hitsave-submission.yaml -> Omeka "Donated by" (dcterms:provenance)."""
from __future__ import annotations

from pathlib import Path

import dump_catalog_omeka as dco
from donor_provenance import donor_names, merged_provenance
from submission_common import load_manifest


def lit(pid, value):
    return {"type": "literal", "property_id": pid, "@value": value, "is_public": True}


def test_donor_names_forms() -> None:
    assert donor_names("Anthony Perkins") == ["Anthony Perkins"]
    assert donor_names("A; B, a") == ["A", "B"]
    assert donor_names(["Brock of Nostalgist's Archive", None, 3, "  x  y "]) == ["Brock of Nostalgist's Archive", "x y"]
    assert donor_names(None) == [] and donor_names("") == []


def test_merge_keeps_existing_donors_and_skips_duplicates() -> None:
    item = {"dcterms:provenance": [lit(51, "Andrew Czudak")]}
    rows = merged_provenance(item, ["andrew czudak", "Anthony Perkins"], 51, lit)
    assert [r["@value"] for r in rows] == ["Andrew Czudak", "Anthony Perkins"]
    assert merged_provenance(None, ["A"], 51, lit) == [lit(51, "A")]


def test_portable_manifest_reads_donor(tmp_path: Path) -> None:
    (tmp_path / "hitsave-submission.yaml").write_text(
        "ingest_kind: press_material\ndonor: [Anthony Perkins, Sir Mikey]\n", encoding="utf-8")
    m = load_manifest(tmp_path, ["hitsave-submission.yaml"], max_manifest_bytes=65536)
    assert m.donor == ("Anthony Perkins", "Sir Mikey")
    (tmp_path / "hitsave-submission.yaml").write_text("ingest_kind: press_material\n", encoding="utf-8")
    assert load_manifest(tmp_path, ["hitsave-submission.yaml"], max_manifest_bytes=65536).donor == ()


class _Api:
    @staticmethod
    def resolve_property_id(session, base, creds, term):
        return {"dcterms:provenance": 51, "dcterms:rights": 15, "dcterms:accessRights": 99}[term]

    literal_value = staticmethod(lit)


def test_dump_values_get_provenance_merged(monkeypatch) -> None:
    monkeypatch.setattr(dco, "_build_dump_catalog_values", lambda *a, **k: ({"dcterms:title": ["t"]}, "T"))
    existing = {"dcterms:provenance": [lit(51, "Staff Added")]}
    values, title = dco.build_dump_catalog_values(None, "b", {}, {"donor": ["Anthony Perkins"]},
                                                  storage_uri_base="", api=_Api, existing_item=existing)
    assert title == "T" and values["dcterms:title"] == ["t"]
    assert [r["@value"] for r in values["dcterms:provenance"]] == ["Staff Added", "Anthony Perkins"]
    plain, _ = dco.build_dump_catalog_values(None, "b", {}, {}, storage_uri_base="", api=_Api, existing_item=existing)
    assert "dcterms:provenance" not in plain  # no donor: registration leaves provenance alone


def test_handoff_donor_reaches_the_dump_manifest(tmp_path: Path) -> None:
    sip = tmp_path / "pkg"
    sip.mkdir()
    (sip / "!submissionInfo.txt").write_text(
        "Disc Identity:\n\tSystem: IBM PC compatible\n\tMedia Type: CD-ROM\n\tCategory: Multimedia\n"
        "\tTitle: Test Disc\n", encoding="utf-8")
    (sip / "disc.iso").write_bytes(b"\0" * 4096)
    handoff = {"dump_class": "redump", "dump_system": "IBM PC compatible", "dump_category": "Multimedia",
               "donor": ["Anthony Perkins"]}
    m = dco.manifest_from_sip_and_handoff(source_dir=sip, game_key="test-disc", omeka_title="Test Disc", handoff=handoff)
    assert m["donor"] == ["Anthony Perkins"]
    assert dco.manifest_from_sip_and_handoff(source_dir=sip, game_key="test-disc", omeka_title="Test Disc",
                                             handoff={k: v for k, v in handoff.items() if k != "donor"})["donor"] == []
