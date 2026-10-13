from __future__ import annotations

from pathlib import Path

from premis_virus_check import (
    VirusScanReport,
    attach_virus_check_premis,
    build_virus_check_premis_xml,
)


def test_build_virus_check_premis_xml_contains_event_type_and_outcome() -> None:
    report = VirusScanReport(
        scanned_at="2026-10-13T04:00:00Z",
        files_scanned=3,
        files_not_scanned=0,
        scanner_program="ClamAV",
        scanner_version="ClamAV 1.4.1/27800",
        virus_definitions="27800",
        sip_root="/data/press-material/F/Example",
    )
    xml = build_virus_check_premis_xml(
        report,
        package_id="example-01a1306f",
        package_uuid="01a1306f-0000-7000-8000-000000000001",
        repository_code="hitsave",
        linking_agent="ingest-folder.py",
    )
    assert "virus check" in xml
    assert "premis:event" in xml
    assert "<premis:eventOutcome>success</premis:eventOutcome>" in xml
    assert "example-01a1306f" in xml
    assert "ingest-folder.py" in xml


def test_attach_virus_check_premis_updates_bag_manifests(tmp_path: Path) -> None:
    bag = tmp_path / "bag"
    (bag / "data").mkdir(parents=True)
    (bag / "data" / "a.txt").write_text("x", encoding="utf-8")
    import bagit

    bagit.make_bag(str(bag), checksums=["sha256"])
    meta = bag / "metadata"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "submission.json").write_text(
        """{
  "package_id": "pkg-1",
  "package_uuid": "01a1306f-1111-7222-8333-444444444444",
  "repository_code": "hitsave"
}
""",
        encoding="utf-8",
    )

    report = VirusScanReport(
        scanned_at="2026-10-13T04:00:00Z",
        files_scanned=1,
        files_not_scanned=0,
        scanner_program="ClamAV",
        scanner_version="test",
    )
    out = attach_virus_check_premis(bag, report, linking_agent="test")
    assert out.is_file()
    manifest = (bag / "manifest-sha256.txt").read_text(encoding="utf-8")
    assert "metadata/premis/virus-check-event.xml" in manifest
