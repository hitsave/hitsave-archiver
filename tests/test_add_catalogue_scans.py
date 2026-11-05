"""add_catalogue_scans_wasabi: pure bag-update helpers."""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import add_catalogue_scans_wasabi as m  # noqa: E402

INV = {"manifest_version": 1, "files": [
    {"path": "data/redump-fwd0501/FWD0501.iso", "size_bytes": 1000, "sha256": "a" * 64, "mime": "application/octet-stream"},
    {"path": "data/redump-fwd0501/!submissionInfo.txt", "size_bytes": 20, "sha256": "b" * 64, "mime": "text/plain"},
], "summary": {"file_count": 2, "total_bytes": 1020, "by_extension": {"iso": 1, "txt": 1}}}
MANIFEST = f"{'a' * 64}  data/redump-fwd0501/FWD0501.iso\n{'b' * 64}  data/redump-fwd0501/!submissionInfo.txt\n"


def test_payload_dir_and_names() -> None:
    assert m.payload_dir(INV) == "redump-fwd0501"
    assert m.safe_name("ファミ通WaveDVD - 2005 January - Disc 1.jpg") == "ファミ通WaveDVD - 2005 January - Disc 1.jpg"
    assert m.safe_name("../a/b\\c.jpg") == "_a_b_c.jpg"
    with pytest.raises(ValueError):
        m.payload_dir({"files": [{"path": "data/x/a"}, {"path": "data/y/b"}]})


def test_plan_skips_identical_bytes_and_refuses_overwrite() -> None:
    manifest = m.parse_manifest(MANIFEST)
    new = [{"name": "Front.jpg", "sha256": "c" * 64, "size": 5}, {"name": "Copy.jpg", "sha256": "a" * 64, "size": 1000},
           {"name": "Again.jpg", "sha256": "c" * 64, "size": 5}]
    add, skip = m.plan_payload_additions(manifest, "redump-fwd0501", new)
    assert [a["rel"] for a in add] == ["data/redump-fwd0501/Scans/Front.jpg"]
    assert len(skip) == 2 and "FWD0501.iso" in skip[0]["reason"]
    manifest["data/redump-fwd0501/Scans/Front.jpg"] = "d" * 64
    with pytest.raises(ValueError):
        m.plan_payload_additions(manifest, "redump-fwd0501", [{"name": "Front.jpg", "sha256": "e" * 64, "size": 1}])


def test_inventory_manifest_oxum_and_tagmanifest() -> None:
    add = [{"rel": "data/redump-fwd0501/Scans/Front.jpg", "sha256": "c" * 64, "size": 5}]
    inv = m.updated_inventory(INV, add)
    assert inv["summary"] == {"file_count": 3, "total_bytes": 1025, "by_extension": {"iso": 1, "jpg": 1, "txt": 1}}
    assert INV["summary"]["file_count"] == 2          # input untouched
    assert inv["files"][-1]["mime"] == "image/jpeg" or any(f["mime"] == "image/jpeg" for f in inv["files"])
    man = m.upsert_lines(MANIFEST, {a["rel"]: a["sha256"] for a in add})
    assert man.splitlines()[:2] == MANIFEST.splitlines() and man.endswith(f"{'c' * 64}  data/redump-fwd0501/Scans/Front.jpg\n")
    info = "Bagging-Date: 2026-10-14\nPayload-Oxum: 1020.2\nInternal-Sender-Identifier: redump-fwd0501\n"
    assert m.set_payload_oxum(info, 1025, 3) == "Bagging-Date: 2026-10-14\nPayload-Oxum: 1025.3\nInternal-Sender-Identifier: redump-fwd0501\n"
    tag = "1  manifest-sha256.txt\n2  bagit.txt\n"
    assert m.upsert_lines(tag, {"manifest-sha256.txt": "9", "metadata/premis/x.xml": "8"}) == "9  manifest-sha256.txt\n2  bagit.txt\n8  metadata/premis/x.xml\n"


def test_ingestion_premis_is_valid_xml_with_sources() -> None:
    xml = m.ingestion_premis_xml(added=[{"rel": "data/p/Scans/Front.jpg", "sha256": "c" * 64, "omeka_media_id": 5471, "omeka_item_id": 5470}],
                                 package_id="pkg", package_uuid="uuid-1", repository_code="hitsave", when="2026-11-05T00:00:00Z",
                                 premis_ns="http://www.loc.gov/premis/v3", xsi_ns="http://www.w3.org/2001/XMLSchema-instance")
    root = ET.fromstring(xml.split("\n", 1)[1])
    P = "{http://www.loc.gov/premis/v3}"
    assert root.find(f"{P}eventType").text == "ingestion"
    assert "Omeka media 5471 (item 5470)" in root.find(f"{P}eventDetailInformation/{P}eventDetail").text
    assert root.find(f"{P}linkingObjectIdentifier/{P}linkingObjectIdentifierValue").text == "uuid-1"
