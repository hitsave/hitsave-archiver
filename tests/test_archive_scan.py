"""Large-archive scanning: every file inside streamed to ClamAV, verified by CRC, no caps."""
from __future__ import annotations

import gzip
import io
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import clamav_full_scan as cfs  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("7zz") is None, reason="7zz not installed")
MARK = b"EVIL-SIGNATURE"
SMALL = dict(max_whole=4_000, chunk=4_000, overlap=200)  # force the archive path on tiny files


class FakeClamd:
    def __init__(self):
        self.streams: list[bytes] = []
        self.timeout = 30

    def scan_stream(self, reader, chunk_size=4096):
        data = b"".join(iter(lambda: reader.read(chunk_size), b""))
        self.streams.append(data)
        return {"stream": ("FOUND", "Fake.Sig")} if MARK in data else None


def _payloads():
    return {
        "a.txt": b"hello " * 500,
        "dir/b.bin": bytes(range(256)) * 30,
        "dir/sub/c.dat": b"\x07" * 9_000,  # bigger than max_whole: chunked inside the archive
    }


def _zip(tmp, files, name="t.zip"):
    p = tmp / name
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        for n, d in files.items():
            z.writestr(n, d)
    return p


def test_zip_every_member_streamed_and_verified(tmp_path):
    files = _payloads()
    p = _zip(tmp_path, files)
    p.write_bytes(p.read_bytes() + b"\0" * 5_000)  # make sure the archive exceeds max_whole
    clamd = FakeClamd()
    res = cfs.scan_path(clamd, p, "t.zip", **SMALL)
    assert res.method == "archive-members" and res.fully_scanned and res.members == 3
    assert files["a.txt"] in clamd.streams  # small member: one exact stream
    b = files["dir/b.bin"]  # 7680 bytes > max_whole: two overlapping chunks of the member
    assert b[:4_000] in clamd.streams and b[4_000 - 200:] in clamd.streams
    assert not any("in the image" in n for n in res.notes)


def test_malware_inside_zip_found(tmp_path):
    files = _payloads() | {"evil.txt": b"x" * 3000 + MARK}
    p = _zip(tmp_path, files)
    p.write_bytes(p.read_bytes() + b"\0" * 5_000)
    with pytest.raises(cfs.MalwareFound):
        cfs.scan_path(FakeClamd(), p, "t.zip", **SMALL)


def test_signature_across_chunk_edge_inside_member_found(tmp_path):
    big = bytearray(b"\x01" * 12_000)
    big[3_995:3_995 + len(MARK)] = MARK  # straddles the first 4000-byte chunk of the member
    p = _zip(tmp_path, {"big.bin": bytes(big)})
    p.write_bytes(p.read_bytes() + b"\0" * 5_000)
    with pytest.raises(cfs.MalwareFound):
        cfs.scan_path(FakeClamd(), p, "t.zip", **SMALL)


def test_solid_7z_streamed_once_with_crc_check(tmp_path):
    src = tmp_path / "src"
    for n, d in _payloads().items():
        (src / n).parent.mkdir(parents=True, exist_ok=True)
        (src / n).write_bytes(d)
    p = tmp_path / "t.7z"
    subprocess.run(["7zz", "a", "-ms=on", "-mx=1", str(p), "."], cwd=src, check=True, capture_output=True)
    p.write_bytes(p.read_bytes())
    pad = 4_001 - p.stat().st_size
    if pad > 0:  # keep it a valid 7z but over max_whole: add an incompressible file
        (src / "pad.bin").write_bytes(__import__("os").urandom(pad + 100))
        subprocess.run(["7zz", "a", "-ms=on", "-mx=1", str(p), "pad.bin"], cwd=src, check=True, capture_output=True)
    res = cfs.scan_path(FakeClamd(), p, "t.7z", **SMALL)
    assert res.method == "archive-members" and res.fully_scanned and res.members >= 3


def test_tar_gz_scanned_file_by_file(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        for n, d in _payloads().items():
            info = tarfile.TarInfo(n); info.size = len(d)
            t.addfile(info, io.BytesIO(d))
    p = tmp_path / "t.tar.gz"
    p.write_bytes(gzip.compress(buf.getvalue(), compresslevel=0))
    clamd = FakeClamd()
    res = cfs.scan_path(clamd, p, "t.tar.gz", **SMALL)
    assert res.method == "archive-members" and res.fully_scanned and res.members == 3
    assert _payloads()["a.txt"] in clamd.streams


def test_plain_gzip_stream_chunked_and_detected(tmp_path):
    data = bytearray(b"\x02" * 20_000)
    data[7_990:7_990 + len(MARK)] = MARK  # across an internal chunk boundary
    p = tmp_path / "web.warc.gz"
    p.write_bytes(gzip.compress(bytes(data), compresslevel=0))
    with pytest.raises(cfs.MalwareFound):
        cfs.scan_path(FakeClamd(), p, "web.warc.gz", **SMALL)


def test_encrypted_entry_marks_partial(tmp_path):
    src = tmp_path / "src"; src.mkdir()
    (src / "secret.txt").write_bytes(b"s" * 6_000)
    (src / "open.txt").write_bytes(b"o" * 6_000)
    p = tmp_path / "t.zip"
    subprocess.run(["7zz", "a", "-pSECRET", "-mx=0", str(p), "secret.txt"], cwd=src, check=True, capture_output=True)
    subprocess.run(["7zz", "a", "-mx=0", str(p), "open.txt"], cwd=src, check=True, capture_output=True)
    clamd = FakeClamd()
    res = cfs.scan_path(clamd, p, "t.zip", **SMALL)
    assert not res.fully_scanned
    assert any("encrypted" in n and "secret.txt" in n for n in res.notes)
    assert b"o" * 4_000 in b"".join(clamd.streams)  # the unencrypted file was still scanned


def test_unsafe_member_name_flagged(tmp_path):
    p = _zip(tmp_path, {"../escape.txt": b"z" * 3000, "ok.txt": b"k" * 3000})
    p.write_bytes(p.read_bytes() + b"\0" * 5_000)
    res = cfs.scan_path(FakeClamd(), p, "t.zip", **SMALL)
    assert any("submission path checks" in n and "escape.txt" in n for n in res.notes)


def test_merge_extents_joins_adjacent_pieces_of_one_file():
    ext = [(0, 10, "a"), (10, 20, "a"), (20, 25, "b"), (30, 40, "a")]
    assert cfs.merge_extents(ext) == [(0, 20, "a"), (20, 25, "b"), (30, 40, "a")]


def test_container_limit_alert_does_not_override_full_member_pass(tmp_path):
    class ContainerAlertClamd(FakeClamd):
        def scan_stream(self, reader, chunk_size=4096):
            data = b"".join(iter(lambda: reader.read(chunk_size), b""))
            self.streams.append(data)
            if data.startswith(b"PK\x03\x04"):  # clamd tries to unpack the truncated container chunk
                return {"stream": ("FOUND", "Heuristics.Limits.Exceeded.MaxFileSize")}
            return None

    p = _zip(tmp_path, _payloads())
    p.write_bytes(p.read_bytes() + b"\0" * 5_000)
    res = cfs.scan_path(ContainerAlertClamd(), p, "t.zip", **SMALL)
    assert res.fully_scanned and res.members == 3
    assert any(n.startswith("container pass alerts") for n in res.notes)
