"""backfill-premis-unscanned.py: correct over-claiming PREMIS virus-check records, touching nothing else."""
from __future__ import annotations

import importlib.util
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("backfill", ROOT / "scripts" / "backfill-premis-unscanned.py")
bf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bf)

LIMIT = 536870912
OLD = """<?xml version="1.0" encoding="UTF-8"?>
<premis:event xmlns:premis="http://www.loc.gov/premis/v3" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" version="3.0" xsi:schemaLocation="http://www.loc.gov/premis/v3 http://www.loc.gov/standards/premis/v3/premis.xsd">
  <premis:eventIdentifier>
    <premis:eventIdentifierType>UUID</premis:eventIdentifierType>
    <premis:eventIdentifierValue>9312f6c6-1df3-4505-b8fd-cf54bda38e38</premis:eventIdentifierValue>
  </premis:eventIdentifier>
  <premis:eventType>virus check</premis:eventType>
  <premis:eventDateTime>2026-10-14T18:58:01Z</premis:eventDateTime>
  <premis:eventDetailInformation>
    <premis:eventDetail>program="ClamAV 1.5.4"; version="ClamAV 1.5.4/28152"; filesScanned=4; virusDefinitions="28152"; sipRoot="/data/x"</premis:eventDetail>
  </premis:eventDetailInformation>
  <premis:eventOutcomeInformation>
    <premis:eventOutcome>success</premis:eventOutcome>
    <premis:eventOutcomeDetailNote>No malware detected (full ClamAV scan before BagIt build).</premis:eventOutcomeDetailNote>
  </premis:eventOutcomeInformation>
  <premis:linkingAgentIdentifier>
    <premis:linkingAgentIdentifierType>software</premis:linkingAgentIdentifierType>
    <premis:linkingAgentIdentifierValue>ingest-folder.py</premis:linkingAgentIdentifierValue>
    <premis:linkingAgentRole>executing program</premis:linkingAgentRole>
  </premis:linkingAgentIdentifier>
  <premis:linkingObjectIdentifier>
    <premis:linkingObjectIdentifierType>local package identifier</premis:linkingObjectIdentifierType>
    <premis:linkingObjectIdentifierValue>pkg-1</premis:linkingObjectIdentifierValue>
    <premis:linkingObjectRole>target</premis:linkingObjectRole>
  </premis:linkingObjectIdentifier>
</premis:event>
"""
P = "{http://www.loc.gov/premis/v3}"


def _text(xml: str, path: str) -> str:
    return ET.fromstring(xml.encode()).find(path).text


def test_oversized_files_from_inventory():
    inv = {"files": [{"path": "data/a.iso", "size_bytes": LIMIT + 1}, {"path": "data/b.txt", "size_bytes": LIMIT},
                     {"path": "data/c.bin", "size_bytes": 5 * LIMIT}]}
    assert bf.oversized_files(inv, LIMIT) == ["data/a.iso", "data/c.bin"]
    assert bf.oversized_files(None, LIMIT) == []


def test_correct_premis_changes_only_what_was_wrong():
    new = bf.correct_premis(OLD, unscanned=["data/x/disc.iso"], limit=LIMIT, corrected_on="2026-11-01")
    assert _text(new, f"{P}eventOutcomeInformation/{P}eventOutcome") == "partial"
    detail = _text(new, f"{P}eventDetailInformation/{P}eventDetail")
    assert f"filesScanned=3; filesNotScanned=1; scanLimitBytes={LIMIT}" in detail
    assert 'program="ClamAV 1.5.4"' in detail and 'sipRoot="/data/x"' in detail
    note = _text(new, f"{P}eventOutcomeInformation/{P}eventOutcomeDetailNote")
    assert "data/x/disc.iso" in note and "NOT scanned" in note
    assert "Record corrected on 2026-11-01" in note and "full ClamAV scan before BagIt build" in note
    # identity and provenance untouched
    assert _text(new, f"{P}eventIdentifier/{P}eventIdentifierValue") == "9312f6c6-1df3-4505-b8fd-cf54bda38e38"
    assert _text(new, f"{P}eventDateTime") == "2026-10-14T18:58:01Z"
    assert _text(new, f"{P}linkingObjectIdentifier/{P}linkingObjectIdentifierValue") == "pkg-1"
    assert new.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<premis:event')


def test_correct_premis_refuses_repeat_or_non_success():
    once = bf.correct_premis(OLD, unscanned=["data/a.iso"], limit=LIMIT, corrected_on="2026-11-01")
    with pytest.raises(ValueError):
        bf.correct_premis(once, unscanned=["data/a.iso"], limit=LIMIT, corrected_on="2026-11-02")
    with pytest.raises(ValueError):
        bf.correct_premis(OLD.replace(">success<", ">failure<"), unscanned=["data/a.iso"], limit=LIMIT, corrected_on="x")


def test_replace_tagmanifest_digest_touches_one_line():
    tm = (
        "aaaa  bag-info.txt\n"
        "bbbb  metadata/premis/virus-check-event.xml\n"
        "cccc  manifest-sha256.txt\n"
    )
    out = bf.replace_tagmanifest_digest(tm, bf.PREMIS_REL, "ffff")
    assert out == "aaaa  bag-info.txt\nffff  metadata/premis/virus-check-event.xml\ncccc  manifest-sha256.txt\n"
    assert bf.tagmanifest_digest(out, bf.PREMIS_REL) == "ffff"
    with pytest.raises(ValueError):
        bf.replace_tagmanifest_digest("aaaa  bag-info.txt\n", bf.PREMIS_REL, "ffff")


class FakeS3:
    def __init__(self, objects):
        self.objects = dict(objects)
        self.puts = []

    def get_object(self, Bucket, Key):  # noqa: N803
        from io import BytesIO
        return {"Body": BytesIO(self.objects[Key])}

    def put_object(self, Bucket, Key, Body, ContentType):  # noqa: N803
        self.puts.append(Key)
        self.objects[Key] = Body


def _row(tmp_path, local=True):
    return {"game_key": "pkg-1", "aip_bag_path": str(tmp_path / "bag") if local else "",
            "inventory_json": {"files": [{"path": "data/x/disc.iso", "size_bytes": LIMIT + 10},
                                         {"path": "data/x/a.txt", "size_bytes": 3}]}}


def _bag(tmp_path, premis: bytes, tm: str):
    (tmp_path / "bag" / "metadata" / "premis").mkdir(parents=True)
    (tmp_path / "bag" / bf.PREMIS_REL).write_bytes(premis)
    (tmp_path / "bag" / bf.TAGMANIFEST).write_text(tm)


def test_process_package_dry_run_writes_nothing(tmp_path):
    premis = OLD.encode()
    tm = f"aaaa  bag-info.txt\n{bf.sha256_hex(premis)}  {bf.PREMIS_REL}\n"
    s3 = FakeS3({f"p/{bf.PREMIS_REL}": premis, f"p/{bf.TAGMANIFEST}": tm.encode()})
    res = bf.process_package(s3, bucket="b", prefix="p", row=_row(tmp_path), limit=LIMIT, execute=False, corrected_on="d")
    assert res["status"] == "would correct" and s3.puts == []


def test_process_package_execute_updates_wasabi_and_matching_local_copy(tmp_path):
    premis = OLD.encode()
    tm = f"aaaa  bag-info.txt\n{bf.sha256_hex(premis)}  {bf.PREMIS_REL}\n"
    _bag(tmp_path, premis, tm)
    s3 = FakeS3({f"p/{bf.PREMIS_REL}": premis, f"p/{bf.TAGMANIFEST}": tm.encode()})
    res = bf.process_package(s3, bucket="b", prefix="p", row=_row(tmp_path), limit=LIMIT, execute=True, corrected_on="d")
    assert res["status"] == "corrected" and res["local_bag"] == "updated"
    assert s3.puts == [f"p/{bf.PREMIS_REL}", f"p/{bf.TAGMANIFEST}"]
    new_premis = s3.objects[f"p/{bf.PREMIS_REL}"]
    new_tm = s3.objects[f"p/{bf.TAGMANIFEST}"].decode()
    assert bf.tagmanifest_digest(new_tm, bf.PREMIS_REL) == bf.sha256_hex(new_premis)
    assert new_tm.startswith("aaaa  bag-info.txt\n")
    assert (tmp_path / "bag" / bf.PREMIS_REL).read_bytes() == new_premis
    assert (tmp_path / "bag" / bf.TAGMANIFEST).read_text() == new_tm


def test_process_package_refuses_inconsistent_bag(tmp_path):
    premis = OLD.encode()
    s3 = FakeS3({f"p/{bf.PREMIS_REL}": premis, f"p/{bf.TAGMANIFEST}": f"dead  {bf.PREMIS_REL}\n".encode()})
    res = bf.process_package(s3, bucket="b", prefix="p", row=_row(tmp_path), limit=LIMIT, execute=True, corrected_on="d")
    assert res["status"].startswith("refused") and s3.puts == []


def test_process_package_leaves_differing_local_copy(tmp_path):
    premis = OLD.encode()
    tm = f"{bf.sha256_hex(premis)}  {bf.PREMIS_REL}\n"
    _bag(tmp_path, b"something else", tm)
    s3 = FakeS3({f"p/{bf.PREMIS_REL}": premis, f"p/{bf.TAGMANIFEST}": tm.encode()})
    res = bf.process_package(s3, bucket="b", prefix="p", row=_row(tmp_path), limit=LIMIT, execute=True, corrected_on="d")
    assert res["status"] == "corrected" and res["local_bag"].startswith("left as is")
    assert (tmp_path / "bag" / bf.PREMIS_REL).read_bytes() == b"something else"
