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
    bag.mkdir(parents=True)
    (bag / "a.txt").write_text("x", encoding="utf-8")
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
    tag_manifest = (bag / "tagmanifest-sha256.txt").read_text(encoding="utf-8")
    assert "metadata/premis/virus-check-event.xml" in tag_manifest


# --- files over the clamd size limit are recorded as NOT scanned ----------------------

from premis_virus_check import clamd_size_limit, scan_sip_tree  # noqa: E402


class _FakeClamd:
    """Streams only (INSTREAM); alerts above max_object like clamd with AlertExceedsMax."""

    def __init__(self, max_object: int = 10**12):
        self.scanned: list[bytes] = []
        self.max_object = max_object
        self.timeout = 30

    def version(self):
        return "ClamAV 1.4.1/27800/Mon Oct 13 2026"

    def scan_stream(self, reader, chunk_size=4096):
        data = b"".join(iter(lambda: reader.read(chunk_size), b""))
        self.scanned.append(data)
        if len(data) > self.max_object:
            return {"stream": ("FOUND", "Heuristics.Limits.Exceeded.MaxScanSize")}
        return None


def test_clamd_size_limit_parses_conf(tmp_path: Path) -> None:
    conf = tmp_path / "clamd.conf"
    conf.write_text("# c\nMaxFileSize 512M\nMaxScanSize 1G\nPCREMaxFileSize 2G\n")
    assert clamd_size_limit(conf) == 512 * 1024**2
    conf.write_text("MaxFileSize 0\nMaxScanSize 0\n")
    assert clamd_size_limit(conf) is None


def test_large_file_fully_scanned_in_chunks_is_success(tmp_path: Path) -> None:
    sip = tmp_path / "sip"
    sip.mkdir()
    (sip / "small.txt").write_bytes(b"x" * 10)
    (sip / "huge.psd").write_bytes(b"y" * 100)
    clamd = _FakeClamd()
    report = scan_sip_tree(clamd, sip, max_whole=50, chunk=40, overlap=5)
    assert report.files_scanned == 2 and report.files_not_scanned == 0
    xml = build_virus_check_premis_xml(
        report, package_id="p", package_uuid="", repository_code="hitsave", linking_agent="t"
    )
    assert "<premis:eventOutcome>success</premis:eventOutcome>" in xml
    assert "Every byte of all 2 file(s)" in xml
    assert "huge.psd (chunked-raw" in xml and "chunkedRawScans=1" in xml


def test_limit_alert_makes_premis_partial(tmp_path: Path) -> None:
    sip = tmp_path / "sip"
    sip.mkdir()
    (sip / "a.txt").write_bytes(b"x" * 10)
    (sip / "nested.zip").write_bytes(b"z" * 30)
    report = scan_sip_tree(_FakeClamd(max_object=20), sip)
    assert report.files_not_scanned == 1 and report.not_scanned_paths == ["nested.zip"]
    xml = build_virus_check_premis_xml(
        report, package_id="p", package_uuid="", repository_code="hitsave", linking_agent="t"
    )
    assert "<premis:eventOutcome>partial</premis:eventOutcome>" in xml
    assert "NOT fully scanned (ClamAV limit alert): nested.zip (Heuristics.Limits.Exceeded" in xml


def test_all_files_scanned_is_success(tmp_path: Path) -> None:
    sip = tmp_path / "sip"
    sip.mkdir()
    (sip / "a.txt").write_bytes(b"x")
    report = scan_sip_tree(_FakeClamd(), sip)
    xml = build_virus_check_premis_xml(
        report, package_id="p", package_uuid="", repository_code="hitsave", linking_agent="t"
    )
    assert "<premis:eventOutcome>success</premis:eventOutcome>" in xml


def test_dotfiles_are_scanned_because_they_go_into_the_bag(tmp_path: Path) -> None:
    sip = tmp_path / "sip"
    (sip / "sub").mkdir(parents=True)
    (sip / "a.txt").write_bytes(b"x")
    (sip / ".checksum.md5").write_bytes(b"x")
    (sip / "sub" / ".DS_Store").write_bytes(b"x")
    clamd = _FakeClamd()
    report = scan_sip_tree(clamd, sip)
    assert report.files_scanned == 3
    assert len(clamd.scanned) == 3
