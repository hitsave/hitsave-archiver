"""Scan every byte of a file with ClamAV, whatever its size.

libclamav refuses any single object larger than ~2 GiB (CLAMD_MAX_OBJECT) regardless of clamd.conf.
Per file, by size and type:

* ``whole``          size <= max_whole: streamed to clamd in one INSTREAM (format-aware scan).
* ``image-members``  larger ISO9660/UDF-bridge disc image: each file in the image's ISO9660
                     directory is streamed on its own (format-aware), and the bytes between files
                     (filesystem structures, anything not listed) are streamed raw.
* ``chunked-raw``    anything else larger (PSD, Switch XCI, …): consecutive raw chunks that overlap
                     by OVERLAP bytes, so a byte signature crossing a chunk edge still lands whole in
                     one chunk. Whole-file hash signatures cannot match a file seen in pieces.

Ranges are visited in ascending offset order, so the SHA-256 of the scanned bytes is computed in the
same pass (callers compare it with the BagIt manifest to prove they scanned the preserved bytes).
A clamd limit alert (Heuristics.Limits.Exceeded.*, needs AlertExceedsMax yes) is never treated as
clean: the file is reported as not fully scanned.
"""
from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field
from pathlib import Path

CLAMD_MAX_OBJECT = 2_147_483_645  # libclamav per-object cap (logged by clamd as "File size limit")
DEFAULT_CHUNK = 1 << 30  # 1 GiB
DEFAULT_OVERLAP = 4 << 20  # 4 MiB
SECTOR = 2048
STREAM_TIMEOUT = 4 * 3600  # seconds clamd may take to answer for one stream
ENCRYPTED_CONTAINERS = {".xci", ".nsp", ".nsz", ".xcz"}


class MalwareFound(RuntimeError):
    """ClamAV reported a signature match."""


@dataclass
class FileScan:
    rel: str
    size: int
    method: str
    streams: int = 0
    members: int = 0
    raw_bytes: int = 0  # bytes scanned outside any listed member (image gaps / chunks)
    fully_scanned: bool = True
    notes: list[str] = field(default_factory=list)
    sha256: str = ""


class _RangeReader:
    """File-like over [start, end) of an open file; feeds the running hash with new bytes only."""

    def __init__(self, fh, start: int, end: int, hasher, hash_state: list[int]):
        self.fh, self.pos, self.end = fh, start, end
        self.hasher, self.hash_state = hasher, hash_state

    def read(self, n: int = -1) -> bytes:
        if self.pos >= self.end:
            return b""
        n = self.end - self.pos if n is None or n < 0 else min(n, self.end - self.pos)
        self.fh.seek(self.pos)
        data = self.fh.read(n)
        if not data:
            raise IOError(f"short read at offset {self.pos}")
        hashed_to = self.hash_state[0]
        if self.pos + len(data) > hashed_to:  # only bytes not yet hashed (ranges overlap by design)
            if self.pos > hashed_to:
                raise RuntimeError(f"hash gap: {hashed_to}..{self.pos} never read")
            self.hasher.update(data[hashed_to - self.pos :])
            self.hash_state[0] = self.pos + len(data)
        self.pos += len(data)
        return data


def _instream(host: str, port: int, reader, *, block: int = 1 << 20) -> dict | None:
    """clamd INSTREAM over TCP with sendall (pyclamd's scan_stream uses send(), which can write
    partially and desynchronise the length-prefixed protocol on large streams)."""
    import socket

    with socket.create_connection((host, int(port)), timeout=STREAM_TIMEOUT) as sock:
        sock.sendall(b"zINSTREAM\0")
        while True:
            data = reader.read(block)
            if not data:
                break
            sock.sendall(struct.pack("!L", len(data)) + data)
        sock.sendall(struct.pack("!L", 0))
        reply = b""
        while not reply.endswith(b"\0"):
            part = sock.recv(4096)
            if not part:
                break
            reply += part
    text = reply.rstrip(b"\0").decode("utf-8", errors="replace").strip()
    if text.endswith(" OK"):
        return None
    if text.endswith(" FOUND"):
        return {"stream": ("FOUND", text.split(": ", 1)[-1][: -len(" FOUND")])}
    return {"stream": ("ERROR", text)}


def _stream(clamd, reader) -> str | None:
    """Stream one object; return a limit alert name, None if clean; raise MalwareFound."""
    if getattr(clamd, "host", None) and getattr(clamd, "port", None):
        result = _instream(clamd.host, clamd.port, reader)
    else:  # test doubles / other clients
        old = getattr(clamd, "timeout", None)
        if old is not None:
            clamd.timeout = STREAM_TIMEOUT
        try:
            result = clamd.scan_stream(reader, chunk_size=1 << 20)
        finally:
            if old is not None:
                clamd.timeout = old
    if not result:
        return None
    for _name, status in result.items():
        code, reason = (status[0], status[1]) if status else ("", "")
        if code == "FOUND" and str(reason).startswith("Heuristics.Limits.Exceeded"):
            return str(reason)
        if code == "FOUND":
            raise MalwareFound(str(reason))
        if code and code not in ("OK", "PASS"):
            raise RuntimeError(f"ClamAV scan error: {status}")
    return None


# --- ISO 9660 directory walk (DVD/BD images carry an ISO9660 bridge next to UDF) ----------------


def iso9660_extents(fh, size: int) -> list[tuple[int, int, str]] | None:
    """[(byte_start, byte_end, path)] for every file extent, or None if not an ISO9660 image."""
    fh.seek(16 * SECTOR)
    pvd = fh.read(SECTOR)
    if len(pvd) < SECTOR or pvd[0] != 1 or pvd[1:6] != b"CD001":
        return None
    root = pvd[156:190]
    out: list[tuple[int, int, str]] = []
    seen: set[int] = set()
    stack = [(struct.unpack_from("<I", root, 2)[0], struct.unpack_from("<I", root, 10)[0], "")]
    while stack:
        lba, length, prefix = stack.pop()
        if lba in seen or lba * SECTOR + length > size or len(seen) > 100_000:
            continue
        seen.add(lba)
        fh.seek(lba * SECTOR)
        data = fh.read(length)
        i = 0
        while i < len(data):
            rec_len = data[i]
            if rec_len == 0:  # records never span sectors: skip to the next one
                i = (i // SECTOR + 1) * SECTOR
                continue
            rec = data[i : i + rec_len]
            i += rec_len
            if len(rec) < 34:
                break
            ext_lba = struct.unpack_from("<I", rec, 2)[0]
            ext_len = struct.unpack_from("<I", rec, 10)[0]
            flags, name_len = rec[25], rec[32]
            name = rec[33 : 33 + name_len]
            if name in (b"\x00", b"\x01"):
                continue
            path = f"{prefix}/{name.decode('latin-1').split(';')[0]}".lstrip("/")
            if flags & 0x02:
                stack.append((ext_lba, ext_len, path))
            elif ext_len:
                start = ext_lba * SECTOR
                out.append((start, min(start + ext_len, size), path))
    return sorted(out)


def _image_plan(extents, size: int, chunk: int, overlap: int, max_whole: int):
    """Ranges in ascending order: ('member', start, end, path) and ('raw', start, end, None)."""
    plan, pos = [], 0

    def raw(start: int, end: int):
        while start < end:
            stop = min(start + chunk, end)
            plan.append(("raw", start, stop, None))
            if stop >= end:
                break
            start = stop - overlap

    for start, end, path in extents:
        if start > pos:
            raw(pos, start)
        if end - start > max_whole:
            raw(start, end)  # a member too big to scan whole (none seen so far)
        else:
            plan.append(("member", start, end, path))
        pos = max(pos, end)
    if pos < size:
        raw(pos, size)
    return plan


def scan_path(
    clamd,
    path: Path,
    rel: str,
    *,
    max_whole: int = CLAMD_MAX_OBJECT,
    chunk: int = DEFAULT_CHUNK,
    overlap: int = DEFAULT_OVERLAP,
) -> FileScan:
    """Scan every byte of ``path`` (see module docstring). Raises MalwareFound on a detection."""
    chunk = min(chunk, max_whole)
    size = path.stat().st_size
    hasher, hash_state = hashlib.sha256(), [0]
    with path.open("rb") as fh:
        if size <= max_whole:
            res = FileScan(rel, size, "whole", streams=1)
            alert = _stream(clamd, _RangeReader(fh, 0, size, hasher, hash_state))
            if alert:
                res.fully_scanned = False
                res.notes.append(alert)
        else:
            if chunk <= overlap:
                raise ValueError("chunk size must exceed the overlap")
            extents = iso9660_extents(fh, size)
            if extents:
                res = FileScan(rel, size, "image-members")
                plan = _image_plan(extents, size, chunk, overlap, max_whole)
            else:
                res = FileScan(rel, size, "chunked-raw")
                plan = _image_plan([], size, chunk, overlap, max_whole)
            last_end = 0
            for kind, start, end, member in plan:
                alert = _stream(clamd, _RangeReader(fh, start, end, hasher, hash_state))
                res.streams += 1
                if kind == "member":
                    res.members += 1
                else:
                    res.raw_bytes += max(end - max(start, last_end), 0)  # unique bytes only
                last_end = max(last_end, end)
                if alert:
                    res.fully_scanned = False
                    res.notes.append(f"{member or f'bytes {start}-{end}'}: {alert}")
            if res.method == "chunked-raw":
                res.notes.append(
                    f"scanned as {res.streams} overlapping raw chunk(s); exceeds the ClamAV "
                    "object limit, so whole-file hash signatures could not apply"
                )
            elif res.raw_bytes:
                res.notes.append(f"{res.members} file(s) in the image scanned individually; "
                                 f"{res.raw_bytes} byte(s) outside them scanned raw")
        if Path(rel).suffix.lower() in ENCRYPTED_CONTAINERS:
            res.notes.append("content is encrypted (console keys); scanned as ciphertext")
    if hash_state[0] != size:
        raise RuntimeError(f"{rel}: scanned {hash_state[0]} of {size} bytes")
    res.sha256 = hasher.hexdigest()
    return res
