"""rescan-virus-unscanned.py: scan what ingest skipped, add a new PREMIS event, touch nothing else."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("rescan", ROOT / "scripts" / "rescan-virus-unscanned.py")
rs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rs)

ORIG = """<?xml version="1.0" encoding="UTF-8"?>
<premis:event xmlns:premis="http://www.loc.gov/premis/v3" version="3.0">
  <premis:eventIdentifier><premis:eventIdentifierType>UUID</premis:eventIdentifierType>
  <premis:eventIdentifierValue>orig-event-uuid</premis:eventIdentifierValue></premis:eventIdentifier>
  <premis:eventType>virus check</premis:eventType>
</premis:event>"""


class FakeClamd:
    def __init__(self, bad: bytes | None = None):
        self.bad = bad

    def version(self):
        return "ClamAV 1.5.4/28170/Sat Oct 31 2026"

    def scan_stream(self, reader, chunk_size=4096):
        data = b"".join(iter(lambda: reader.read(chunk_size), b""))
        return {"stream": ("FOUND", "Fake.Sig")} if self.bad and self.bad in data else None


class FakeS3:
    def __init__(self, objects):
        self.objects = dict(objects)
        self.puts = []

    def get_object(self, Bucket, Key):  # noqa: N803
        return {"Body": BytesIO(self.objects[Key])}

    def put_object(self, Bucket, Key, Body, ContentType):  # noqa: N803
        self.puts.append(Key)
        self.objects[Key] = Body


def _setup(tmp_path, payload: bytes, manifest_digest: str | None = None):
    bag = tmp_path / "bag"
    (bag / "data").mkdir(parents=True)
    (bag / "data" / "disc.iso").write_bytes(payload)
    tm = "aaaa  bag-info.txt\nbbbb  metadata/premis/virus-check-event.xml\n"
    (bag / rs.TAGMANIFEST).write_text(tm)
    digest = manifest_digest or hashlib.sha256(payload).hexdigest()
    s3 = FakeS3({
        f"p/{rs.TAGMANIFEST}": tm.encode(),
        f"p/{rs.MANIFEST}": f"{digest}  data/disc.iso\n".encode(),
        f"p/{rs.ORIG_PREMIS}": ORIG.encode(),
        "p/metadata/submission.json": json.dumps({"package_id": "pkg", "repository_code": "hitsave"}).encode(),
    })
    row = {"game_key": "pkg", "aip_bag_path": str(bag),
           "inventory_json": {"files": [{"path": "data/disc.iso", "size_bytes": rs.LEGACY_LIMIT + 1},
                                        {"path": "data/small.txt", "size_bytes": 10}]}}
    return bag, s3, row


def test_targets_only_files_ingest_skipped():
    inv = {"files": [{"path": "data/a.iso", "size_bytes": rs.LEGACY_LIMIT + 1},
                     {"path": "data/b.txt", "size_bytes": rs.LEGACY_LIMIT}]}
    assert rs.targets(inv) == ["data/a.iso"]


def test_add_tagmanifest_line_appends_once():
    out = rs.add_tagmanifest_line("aaaa  bag-info.txt\n", "metadata/premis/x.xml", "ffff")
    assert out == "aaaa  bag-info.txt\nffff  metadata/premis/x.xml\n"
    import pytest
    with pytest.raises(ValueError):
        rs.add_tagmanifest_line(out, "metadata/premis/x.xml", "eeee")


def test_execute_writes_new_event_and_tagmanifest_line_only(tmp_path):
    bag, s3, row = _setup(tmp_path, b"disc bytes" * 100)
    res = rs.process(lambda: FakeClamd(), s3, row=row, bucket="b", prefix="p", execute=True, on="2026-11-01")
    assert res["status"] == "rescanned" and res["local_bag"] == "updated"
    rel = "metadata/premis/virus-check-rescan-2026-11-01.xml"
    assert s3.puts == [f"p/{rel}", f"p/{rs.TAGMANIFEST}"]  # nothing else written
    premis = s3.objects[f"p/{rel}"].decode()
    assert "<premis:eventOutcome>success</premis:eventOutcome>" in premis
    assert 'supplementsEvent="orig-event-uuid"' in premis and "Rescan of the 1 file(s)" in premis
    tm = s3.objects[f"p/{rs.TAGMANIFEST}"].decode()
    assert tm.startswith("aaaa  bag-info.txt\nbbbb  metadata/premis/virus-check-event.xml\n")
    assert rs.parse_manifest(tm)[rel] == hashlib.sha256(s3.objects[f"p/{rel}"]).hexdigest()
    assert (bag / rel).read_bytes() == s3.objects[f"p/{rel}"]
    # re-running is a no-op
    again = rs.process(lambda: FakeClamd(), s3, row=row, bucket="b", prefix="p", execute=True, on="2026-11-02")
    assert again["status"].startswith("skipped")


def test_refuses_when_scanned_bytes_differ_from_manifest(tmp_path):
    _, s3, row = _setup(tmp_path, b"local copy", manifest_digest="0" * 64)
    res = rs.process(lambda: FakeClamd(), s3, row=row, bucket="b", prefix="p", execute=True, on="d")
    assert res["status"].startswith("refused") and s3.puts == []


def test_malware_stops_the_package_and_writes_nothing(tmp_path):
    _, s3, row = _setup(tmp_path, b"xx EVIL xx")
    res = rs.process(lambda: FakeClamd(bad=b"EVIL"), s3, row=row, bucket="b", prefix="p", execute=True, on="d")
    assert res["status"].startswith("MALWARE") and s3.puts == []


def test_dry_run_writes_nothing(tmp_path):
    _, s3, row = _setup(tmp_path, b"x")
    res = rs.process(lambda: FakeClamd(), s3, row=row, bucket="b", prefix="p", execute=False, on="d")
    assert res["status"] == "would rescan" and s3.puts == []
