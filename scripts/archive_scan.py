"""Stream every file inside a large archive to ClamAV (used by clamav_full_scan for archives over
libclamav's ~2 GiB object limit; smaller archives are unpacked by clamd itself).

* Multi-file formats (7z, zip, rar, tar): ``7zz x -so`` writes all files to one stream in listing
  order, decompressing once (solid 7z archives are not re-decompressed per file). The stream is
  cut into files by the listed sizes and every cut is verified against the listed CRC32.
* Single-stream formats (gzip, bzip2, xz, zstd): decompressed and scanned to the end of the data
  (gzip stores the size modulo 4 GiB, so listed sizes are not trusted); a tar inside is scanned
  file by file.
* No size or count caps. Nothing is written to disk, so path tricks cannot do harm, but unsafe
  member names are still flagged with the submission checks (submission_common).
* Never reported as clean when not fully scanned: encrypted entries, CRC/length mismatches,
  7-Zip errors on unverified data, and nested archives over 2 GiB mark the file partial.
"""
from __future__ import annotations

import subprocess
import tarfile
import tempfile
import zlib
from pathlib import Path

SEVEN_ZIP = "7zz"
NO_PASSWORD = "-pHITSAVE-NO-PASSWORD"  # never prompt; encrypted data simply fails
MAGIC = [
    (b"7z\xbc\xaf\x27\x1c", "7z"), (b"PK\x03\x04", "zip"), (b"PK\x05\x06", "zip"),
    (b"Rar!\x1a\x07\x01\x00", "rar"), (b"Rar!\x1a\x07\x00", "rar"),
    (b"\x1f\x8b", "gzip"), (b"BZh", "bzip2"), (b"\xfd7zXZ\x00", "xz"), (b"\x28\xb5\x2f\xfd", "zstd"),
]
SINGLE_STREAM = {"gzip", "bzip2", "xz", "zstd"}


def archive_kind(fh) -> str | None:
    fh.seek(0)
    head = fh.read(8)
    for magic, kind in MAGIC:
        if head.startswith(magic):
            return kind
    fh.seek(257)
    if fh.read(5) == b"ustar":
        return "tar"
    return None


def list_entries(path: Path) -> list[dict]:
    out = subprocess.run([SEVEN_ZIP, "l", "-slt", "-ba", NO_PASSWORD, str(path)],
                         capture_output=True, text=True, errors="replace").stdout
    entries, cur = [], {}
    for line in out.splitlines() + [""]:
        if not line.strip():
            if "Path" in cur and "Size" in cur and "Type" not in cur:
                entries.append(cur)
            cur = {}
            continue
        key, sep, value = line.partition(" = ")
        if sep:
            cur[key.strip()] = value
    return entries


class _Pushback:
    """read(n) over a pipe, reading fully, with unread() for one-byte EOF peeks."""

    def __init__(self, raw):
        self.raw, self.buf = raw, b""

    def read(self, n: int) -> bytes:
        out = self.buf[:n]
        self.buf = self.buf[n:]
        while len(out) < n:
            part = self.raw.read(n - len(out))
            if not part:
                break
            out += part
        return out

    def unread(self, data: bytes) -> None:
        self.buf = data + self.buf


class _Bounded:
    """At most ``n`` bytes from ``src`` (None = until EOF); tracks CRC32 and bytes delivered."""

    def __init__(self, src, n: int | None):
        self.src, self.left, self.crc, self.count = src, n, 0, 0

    def read(self, k: int) -> bytes:
        if self.left is not None:
            k = min(k, self.left)
        data = self.src.read(k) if k > 0 else b""
        self.crc = zlib.crc32(data, self.crc)
        self.count += len(data)
        if self.left is not None:
            self.left -= len(data)
        return data

    def at_end(self) -> bool:
        if self.left == 0:
            return True
        peek = self.src.read(1)
        if not peek:
            return True
        self.src.unread(peek)
        return False


class _Chunk:
    """One clamd stream: the previous chunk's tail (overlap) then up to ``budget`` new bytes."""

    def __init__(self, src: _Bounded, carry: bytes, budget: int, overlap: int):
        self.src, self.carry, self.budget, self.overlap = src, carry, budget, overlap
        self.tail = bytearray(carry[-overlap:] if overlap else b"")
        self.new = 0

    def read(self, n: int = 1 << 20) -> bytes:
        if self.carry:
            out, self.carry = self.carry[:n], self.carry[n:]
            return out
        if self.new >= self.budget:
            return b""
        data = self.src.read(min(n, self.budget - self.new))
        self.new += len(data)
        if self.overlap and data:
            self.tail += data
            del self.tail[: max(0, len(self.tail) - self.overlap)]
        return data


def _stream_member(clamd, src: _Bounded, *, label: str, res, chunk: int, overlap: int) -> int:
    """Stream one file inside the archive (overlapping chunks when bigger than ``chunk``)."""
    from clamav_full_scan import _stream

    carry, streams = b"", 0
    while True:
        part = _Chunk(src, carry, chunk - len(carry), overlap)
        alert = _stream(clamd, part)
        streams += 1
        if alert:
            res.fully_scanned = False
            res.notes.append(f"{label}: {alert}")
        if src.at_end() or part.new == 0:
            break
        carry = bytes(part.tail)
    return streams


def _flag_name(name: str, res, flagged: list) -> None:
    from submission_common import SubmissionError, _safe_member_name

    try:
        _safe_member_name(name, 10**9)  # submission path checks, no length cap
    except SubmissionError:
        flagged.append(name)


def _nested_big_archive(first: bytes) -> bool:
    return any(first.startswith(m) for m, _ in MAGIC) or first[257:262] == b"ustar"


def scan_archive_members(clamd, path: Path, kind: str, res, *, max_whole: int, chunk: int,
                         overlap: int) -> None:
    """Stream every file inside ``path`` to clamd; updates ``res`` (members, notes, fully_scanned)."""
    unsafe: list[str] = []
    scanned_files = 0
    total = 0
    err = tempfile.TemporaryFile()
    if kind in SINGLE_STREAM:
        proc = subprocess.Popen([SEVEN_ZIP, "x", "-so", "-bd", "-y", NO_PASSWORD, str(path)],
                                stdout=subprocess.PIPE, stderr=err)
        src = _Pushback(proc.stdout)
        head = src.read(512)
        src.unread(head)
        if len(head) == 512 and head[257:262] == b"ustar":
            with tarfile.open(fileobj=src, mode="r|") as tar:
                for info in tar:
                    _flag_name(info.name, res, unsafe)
                    if not info.isfile():
                        continue
                    member = _Bounded(_Pushback(tar.extractfile(info)), info.size)
                    _stream_member(clamd, member, label=info.name, res=res, chunk=chunk, overlap=overlap)
                    if member.count != info.size:
                        res.fully_scanned = False
                        res.notes.append(f"{info.name}: short read in tar stream")
                    scanned_files += 1
                    total += member.count
        else:
            member = _Bounded(src, None)
            streams = _stream_member(clamd, member, label="decompressed stream", res=res,
                                     chunk=chunk, overlap=overlap)
            scanned_files, total = 1, member.count
            if streams > 1:
                res.notes.append(f"decompressed stream ({member.count} bytes) scanned in {streams} "
                                 "overlapping chunk(s)")
        rc = proc.wait()
        if rc != 0:
            res.fully_scanned = False
            res.notes.append(f"7-Zip exited {rc} while decompressing: {_tail(err)}")
    else:
        entries = list_entries(path)
        files = [e for e in entries if e.get("Folder") != "+" and not e.get("Attributes", "").startswith("D")]
        for e in entries:
            _flag_name(e["Path"], res, unsafe)
        encrypted = [e for e in files if e.get("Encrypted") == "+"]
        if not entries:
            res.fully_scanned = False
            res.notes.append("7-Zip could not list the archive (encrypted headers or damaged)")
            return
        if encrypted:
            res.fully_scanned = False
            res.notes.append(f"{len(encrypted)} encrypted file(s) inside could not be scanned: "
                             + ", ".join(e["Path"] for e in encrypted[:10]))
        plain = [e for e in files if e.get("Encrypted") != "+"]
        streams_per_entry = [plain] if not encrypted else [[e] for e in plain]
        for group in streams_per_entry:
            cmd = [SEVEN_ZIP, "x" if len(group) > 1 or not encrypted else "e", "-so", "-bd", "-y",
                   NO_PASSWORD, str(path)] + ([] if not encrypted else ["--", group[0]["Path"]])
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err)
            src = _Pushback(proc.stdout)
            verified_all = True
            for e in group:
                size = int(e.get("Size") or 0)
                member = _Bounded(src, size)
                if size:
                    first = src.read(min(size, 512))
                    src.unread(first)
                    if size > max_whole and _nested_big_archive(first):
                        res.fully_scanned = False
                        res.notes.append(f"{e['Path']}: nested archive over 2 GiB scanned as raw chunks")
                    _stream_member(clamd, member, label=e["Path"], res=res, chunk=chunk, overlap=overlap)
                if member.count != size:
                    raise RuntimeError(f"{e['Path']}: got {member.count} of {size} bytes from 7-Zip "
                                       "(stream/listing out of step)")
                want_crc = (e.get("CRC") or "").strip()
                if want_crc:
                    if int(want_crc, 16) != member.crc & 0xFFFFFFFF:
                        raise RuntimeError(f"{e['Path']}: CRC mismatch (stream/listing out of step)")
                elif size:
                    verified_all = False
                scanned_files += 1
                total += member.count
            leftover = src.read(1)
            rc = proc.wait()
            if leftover:
                raise RuntimeError("7-Zip produced more data than listed")
            if rc != 0 and not verified_all:
                res.fully_scanned = False
                res.notes.append(f"7-Zip exited {rc} and some data had no CRC to verify: {_tail(err)}")
            elif rc != 0:
                res.notes.append(f"7-Zip reported warnings (exit {rc}); every file's CRC verified")
    res.members += scanned_files
    res.notes.append(f"{scanned_files} file(s) inside the {kind} archive scanned individually "
                     f"({total} bytes decompressed); container bytes also scanned raw")
    if unsafe:
        res.notes.append(f"{len(unsafe)} member name(s) fail the submission path checks "
                         f"(not extracted, so harmless here): {unsafe[:5]}")


def _tail(err) -> str:
    err.seek(0)
    text = err.read().decode("utf-8", errors="replace").strip().splitlines()
    return " | ".join(line for line in text[-4:] if line)[:300]
