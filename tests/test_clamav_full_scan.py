"""clamav_full_scan: every byte scanned once (plus chunk overlaps), whatever the file size."""
from __future__ import annotations

import hashlib
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import clamav_full_scan as cfs  # noqa: E402

MARK = b"EVIL-SIGNATURE"


class FakeClamd:
    """Reads each stream fully; alerts above max_object; 'detects' MARK."""

    def __init__(self, max_object: int):
        self.max_object = max_object
        self.streams: list[bytes] = []
        self.timeout = 30

    def scan_stream(self, reader, chunk_size=4096):
        assert self.timeout == cfs.STREAM_TIMEOUT
        data = b""
        while True:
            block = reader.read(chunk_size)
            if not block:
                break
            data += block
        self.streams.append(data)
        if len(data) > self.max_object:
            return {"stream": ("FOUND", "Heuristics.Limits.Exceeded.MaxFileSize")}
        if MARK in data:
            return {"stream": ("FOUND", "Fake.Signature")}
        return None


def _write(tmp_path: Path, name: str, data: bytes) -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_small_file_scanned_whole(tmp_path):
    data = bytes(range(256)) * 10
    clamd = FakeClamd(max_object=10_000)
    res = cfs.scan_path(clamd, _write(tmp_path, "a.bin", data), "a.bin", max_whole=10_000)
    assert res.method == "whole" and res.fully_scanned and clamd.streams == [data]
    assert res.sha256 == hashlib.sha256(data).hexdigest()
    assert clamd.timeout == 30  # restored


def test_large_file_chunked_with_overlap_covers_every_byte(tmp_path):
    data = bytes((i * 7) % 251 for i in range(10_000))
    clamd = FakeClamd(max_object=3_000)
    res = cfs.scan_path(clamd, _write(tmp_path, "big.psd", data), "big.psd",
                        max_whole=3_000, chunk=3_000, overlap=100)
    assert res.method == "chunked-raw" and res.fully_scanned
    assert all(len(s) <= 3_000 for s in clamd.streams)
    # chunks overlap by exactly 100 bytes and together cover the file
    covered = b"".join([clamd.streams[0]] + [s[100:] for s in clamd.streams[1:]])
    assert covered == data
    assert res.sha256 == hashlib.sha256(data).hexdigest()
    assert "whole-file hash signatures" in " ".join(res.notes)


def test_signature_across_chunk_edge_is_found(tmp_path):
    data = bytearray(10_000)
    data[2_995:2_995 + len(MARK)] = MARK  # straddles the 3000-byte chunk edge
    clamd = FakeClamd(max_object=3_000)
    with pytest.raises(cfs.MalwareFound):
        cfs.scan_path(clamd, _write(tmp_path, "x.psd", bytes(data)), "x.psd",
                      max_whole=3_000, chunk=3_000, overlap=100)


def test_limit_alert_means_not_fully_scanned(tmp_path):
    clamd = FakeClamd(max_object=10)  # clamd rejects even the "whole" stream
    res = cfs.scan_path(clamd, _write(tmp_path, "a.bin", b"x" * 50), "a.bin", max_whole=1_000)
    assert not res.fully_scanned and "Heuristics.Limits.Exceeded" in res.notes[0]


def test_encrypted_container_noted(tmp_path):
    clamd = FakeClamd(max_object=10_000)
    res = cfs.scan_path(clamd, _write(tmp_path, "game.xci", b"x" * 100), "game.xci", max_whole=10_000)
    assert any("encrypted" in n for n in res.notes)


# --- disc images ---------------------------------------------------------------------------------

S = cfs.SECTOR


def _dir_record(name: bytes, lba: int, size: int, is_dir: bool) -> bytes:
    rec = bytearray(33 + len(name) + (1 - len(name) % 2))
    rec[0] = len(rec)
    struct.pack_into("<I", rec, 2, lba); struct.pack_into(">I", rec, 6, lba)
    struct.pack_into("<I", rec, 10, size); struct.pack_into(">I", rec, 14, size)
    rec[25] = 0x02 if is_dir else 0
    rec[32] = len(name)
    rec[33:33 + len(name)] = name
    return bytes(rec)


def _iso(files: dict[str, bytes], total_sectors: int) -> bytes:
    """Minimal ISO9660: PVD @16, root dir @18 holding files at increasing sectors from 20."""
    img = bytearray(total_sectors * S)
    lba, recs = 20, [_dir_record(b"\x00", 18, S, True), _dir_record(b"\x01", 18, S, True)]
    for name, content in files.items():
        img[lba * S: lba * S + len(content)] = content
        recs.append(_dir_record(name.encode() + b";1", lba, len(content), False))
        lba += -(-len(content) // S) + 1  # leave a one-sector gap after each file
    root_dir = b"".join(recs)
    img[18 * S: 18 * S + len(root_dir)] = root_dir
    pvd = bytearray(S)
    pvd[0] = 1; pvd[1:6] = b"CD001"; pvd[6] = 1
    pvd[156:190] = _dir_record(b"\x00", 18, S, True)
    img[16 * S: 17 * S] = pvd
    return bytes(img)


def test_iso9660_extents_lists_files():
    img = _iso({"A.VOB": b"a" * 5000, "B.VOB": b"b" * 3000}, 40)
    import io
    ext = cfs.iso9660_extents(io.BytesIO(img), len(img))
    assert [(s // S, e - s, p) for s, e, p in ext] == [(20, 5000, "A.VOB"), (24, 3000, "B.VOB")]


def test_large_disc_image_scanned_member_by_member_plus_gaps(tmp_path):
    a, b = bytes(range(256)) * 20, b"\x07" * 3000
    img = _iso({"A.VOB": a, "B.VOB": b}, 40)
    clamd = FakeClamd(max_object=20_000)
    res = cfs.scan_path(clamd, _write(tmp_path, "d.iso", img), "d.iso",
                        max_whole=20_000, chunk=20_000, overlap=512)
    assert res.method == "image-members" and res.fully_scanned and res.members == 2
    assert a in clamd.streams and b in clamd.streams  # each file streamed exactly as itself
    assert res.raw_bytes == len(img) - len(a) - len(b)  # every other byte scanned raw, once
    assert res.sha256 == hashlib.sha256(img).hexdigest()


def test_malware_inside_disc_member_found(tmp_path):
    img = _iso({"A.VOB": b"x" * 1000 + MARK, "B.VOB": b"ok" * 100}, 30)
    with pytest.raises(cfs.MalwareFound):
        cfs.scan_path(FakeClamd(max_object=20_000), _write(tmp_path, "d.iso", img), "d.iso",
                      max_whole=20_000, chunk=20_000, overlap=512)


def test_large_non_iso_falls_back_to_chunks(tmp_path):
    data = b"\x01" * 50_000  # no CD001 at sector 16
    res = cfs.scan_path(FakeClamd(max_object=20_000), _write(tmp_path, "big.bin", data), "big.bin",
                        max_whole=20_000, chunk=20_000, overlap=512)
    assert res.method == "chunked-raw" and res.fully_scanned
