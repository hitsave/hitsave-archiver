"""PREMIS events recorded inside each bag: ingestion, digests, format identification, later changes."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import premis_events as pe

P = "{http://www.loc.gov/premis/v3}"


def _event(**kw) -> pe.PremisEvent:
    base = dict(
        event_type="metadata modification",
        detail="Replaced the DVD menu preview with a contact sheet.",
        program="apply_dvd_menu_bag_previews.py",
        package_id="lara-croft-01",
        package_uuid="0194-uuid",
        repository_code="hitsave",
        when="2026-11-09T03:12:05Z",
        event_id="5f0c2b9e-1111-2222-3333-444455556666",
    )
    base.update(kw)
    return pe.PremisEvent(**base)


def test_event_xml_has_type_time_detail_outcome_agents_and_objects() -> None:
    root = ET.fromstring(pe.event_xml(_event(outcome_note="2 files changed")))
    assert root.tag == f"{P}event" and root.get("version") == "3.0"
    assert root.findtext(f"{P}eventIdentifier/{P}eventIdentifierValue") == "5f0c2b9e-1111-2222-3333-444455556666"
    assert root.findtext(f"{P}eventType") == "metadata modification"
    assert root.findtext(f"{P}eventDateTime") == "2026-11-09T03:12:05Z"
    assert root.findtext(f"{P}eventDetailInformation/{P}eventDetail").startswith("Replaced the DVD menu")
    assert root.findtext(f"{P}eventOutcomeInformation/{P}eventOutcome") == "success"
    assert root.findtext(f"{P}eventOutcomeInformation/{P}eventOutcomeDetailNote") == "2 files changed"
    agents = {(a.findtext(f"{P}linkingAgentIdentifierType"), a.findtext(f"{P}linkingAgentIdentifierValue"),
               a.findtext(f"{P}linkingAgentRole")) for a in root.findall(f"{P}linkingAgentIdentifier")}
    assert agents == {("software", "apply_dvd_menu_bag_previews.py", "executing program"),
                      ("repository code", "hitsave", "implementer")}
    objects = {(o.findtext(f"{P}linkingObjectIdentifierType"), o.findtext(f"{P}linkingObjectIdentifierValue"))
               for o in root.findall(f"{P}linkingObjectIdentifier")}
    assert objects == {("local package identifier", "lara-croft-01"), ("UUID", "0194-uuid")}


def test_event_needs_a_package_id() -> None:
    with pytest.raises(ValueError):
        pe.event_xml(_event(package_id=""))


def test_relpath_is_unique_per_event_and_sorts_by_time() -> None:
    a = pe.event_relpath("dvd-menu-preview", _event())
    b = pe.event_relpath("dvd-menu-preview", _event(event_id="9a9a9a9a-0000-0000-0000-000000000000"))
    assert a == "metadata/premis/dvd-menu-preview-20261109T031205Z-5f0c2b9e.xml"
    assert a != b
    assert pe.event_relpath("ingestion", _event(when="2026-01-02T00:00:00Z")) < a.replace("dvd-menu-preview", "ingestion")


def test_stage_event_appends_one_tagmanifest_line() -> None:
    tm = "aaa  bag-info.txt\nbbb  metadata/submission.json\n"
    rel, body, new_tm = pe.stage_event(tm, _event(), "dvd-menu-preview")
    assert rel == "metadata/premis/dvd-menu-preview-20261109T031205Z-5f0c2b9e.xml"
    assert new_tm == tm + f"{pe.sha256_hex(body)}  {rel}\n"
    assert ET.fromstring(body).findtext(f"{P}eventType") == "metadata modification"


def test_stage_event_handles_missing_final_newline_and_refuses_duplicates() -> None:
    rel, body, new_tm = pe.stage_event("aaa  bag-info.txt", _event(), "x")
    assert new_tm.splitlines() == ["aaa  bag-info.txt", f"{pe.sha256_hex(body)}  {rel}"]
    with pytest.raises(ValueError):
        pe.stage_event(new_tm, _event(), "x")


def test_add_event_file_writes_into_work_and_registers_it(tmp_path: Path) -> None:
    files: dict[str, Path] = {"metadata/hitsave/preview-manifest.json": tmp_path / "m.json"}
    rel = pe.add_event_file(files, tmp_path, _event(), "dvd-menu-preview")
    assert files[rel] == tmp_path / rel and (tmp_path / rel).is_file()
    assert ET.parse(tmp_path / rel).getroot().findtext(f"{P}eventType") == "metadata modification"


def test_write_event_puts_file_in_bag_metadata(tmp_path: Path) -> None:
    path = pe.write_event(tmp_path, _event(event_type="ingestion"), "ingestion")
    assert path.parent == tmp_path / "metadata" / "premis"
    assert ET.parse(path).getroot().findtext(f"{P}eventType") == "ingestion"


def test_repository_code_falls_back_when_config_is_missing(monkeypatch) -> None:
    def boom():
        raise FileNotFoundError
    monkeypatch.setattr(pe, "load_ingest_config", boom)
    assert pe.repository_code() == "hitsave"
    monkeypatch.setattr(pe, "load_ingest_config", lambda: {"preservation": {"repository_code": "hs-test"}})
    assert pe.repository_code() == "hs-test"


def _build(tmp_path: Path, monkeypatch, formats=None):
    import importlib.util
    import format_identification as fid
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("bbp_premis", root / "scripts" / "build-bagit-package.py")
    bbp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bbp)
    sip = tmp_path / "Some Game"
    sip.mkdir()
    (sip / "readme.txt").write_text("hello\n")
    (sip / "disc.iso").write_bytes(b"iso")
    identity = type("I", (), {})()
    for k, v in dict(package_id="pkg-1", package_uuid="uuid-1", relative_sip_path=sip.name, sip_fingerprint="f",
                     title_slug="t", sort_letter="S", omeka_item_title="T").items():
        setattr(identity, k, v)
    identity.external_identifier = lambda: "hitsave:pkg-1"
    monkeypatch.setattr(bbp, "load_packaging_config", lambda: {"bagit": {}})
    monkeypatch.setattr(bbp, "load_ingest_config", lambda: {"wasabi": {}})
    monkeypatch.setattr(bbp, "resolve_submission_source", lambda s: {"id": "hitsave-submission:x", "type": "t"})
    monkeypatch.setattr(bbp, "resolve_wasabi_bucket", lambda w, k: "b")
    monkeypatch.setattr(bbp, "load_preview_config", lambda: {})
    monkeypatch.setattr(bbp, "write_hitsave_previews", lambda *a, **k: None)
    run = formats or fid.FormatRun(
        status="ok", tool="siegfried 1.11.9", signature="default.sig", identified_at="2026-11-09T00:00:00Z",
        hits={"readme.txt": fid.FormatHit(puid="x-fmt/111"), "disc.iso": fid.FormatHit(puid=None)},
    )
    monkeypatch.setattr(bbp, "identify_tree", lambda r: run)
    bag = tmp_path / "bag"
    bbp.build_bagit_package(sip, bag, identity=identity, agent_version="build-bagit-package.py",
                            repository_code="hitsave", max_files=10, max_bytes=0)
    return bag


def _events(bag: Path) -> dict[str, ET.Element]:
    return {ET.parse(p).getroot().findtext(f"{P}eventType"): ET.parse(p).getroot()
            for p in sorted((bag / "metadata" / "premis").glob("*.xml"))}


def test_built_bag_records_ingestion_digest_and_format_events(tmp_path: Path, monkeypatch) -> None:
    import bagit
    bag = _build(tmp_path, monkeypatch)
    events = _events(bag)
    assert set(events) == {"ingestion", "message digest calculation", "format identification"}
    assert "hitsave-submission:x" in events["ingestion"].findtext(f"{P}eventDetailInformation/{P}eventDetail")
    digest = events["message digest calculation"].findtext(f"{P}eventDetailInformation/{P}eventDetail")
    assert "SHA-256" in digest and "2 payload files" in digest
    fmt = events["format identification"]
    assert fmt.findtext(f"{P}eventOutcomeInformation/{P}eventOutcome") == "success"
    assert "1 of 2" in fmt.findtext(f"{P}eventOutcomeInformation/{P}eventOutcomeDetailNote")
    tagmanifest = (bag / "tagmanifest-sha256.txt").read_text()
    assert all(f"metadata/premis/{p.name}" in tagmanifest for p in (bag / "metadata" / "premis").glob("*.xml"))
    bagit.Bag(str(bag)).validate()


def test_failed_format_identification_is_a_failure_event(tmp_path: Path, monkeypatch) -> None:
    import format_identification as fid
    bag = _build(tmp_path, monkeypatch, formats=fid.FormatRun(status="failed", error="sf: not found"))
    fmt = _events(bag)["format identification"]
    assert fmt.findtext(f"{P}eventOutcomeInformation/{P}eventOutcome") == "failure"
    assert "sf: not found" in fmt.findtext(f"{P}eventOutcomeInformation/{P}eventOutcomeDetailNote")
