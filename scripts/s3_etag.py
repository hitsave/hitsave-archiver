"""Local SHA-256 + expected S3/Wasabi ETag for a file, in one read.

S3-compatible stores (Wasabi included) set ETag = MD5 of the bytes for single-part uploads,
and MD5(concatenated part MD5s) + "-<parts>" for multipart uploads. Comparing the expected
ETag with HEAD after upload proves the stored bytes equal the local file without
downloading it, which matters for multi-GB disc images.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path

# Must match upload_bag_wasabi.S3_TRANSFER (multipart_threshold == multipart_chunksize).
DEFAULT_PART_SIZE = 64 * 1024 * 1024
_READ = 8 * 1024 * 1024
_S3_MAX_PARTS = 10_000


@dataclass(frozen=True)
class FileHashes:
    size: int
    sha256: str
    md5: str
    part_size: int
    part_md5s: tuple[bytes, ...]

    @property
    def parts(self) -> int:
        return len(self.part_md5s)

    def multipart_etag(self) -> str | None:
        if not self.part_md5s:
            return None
        return f"{hashlib.md5(b''.join(self.part_md5s)).hexdigest()}-{len(self.part_md5s)}"

    def expected_etag(self, *, multipart_threshold: int | None = None) -> str:
        """ETag boto3's transfer manager produces for this file with our TransferConfig."""
        threshold = self.part_size if multipart_threshold is None else multipart_threshold
        if self.size < threshold:
            return self.md5
        return self.multipart_etag() or self.md5


def hash_file(path: Path | str, *, part_size: int = DEFAULT_PART_SIZE) -> FileHashes:
    size = Path(path).stat().st_size
    if size and math.ceil(size / part_size) > _S3_MAX_PARTS:
        # boto3 would enlarge the part size; we can't predict the ETag reliably.
        raise ValueError(f"{path}: {size} bytes needs more than {_S3_MAX_PARTS} parts at {part_size}")
    sha = hashlib.sha256()
    whole = hashlib.md5()
    parts: list[bytes] = []
    part = hashlib.md5()
    in_part = 0
    total = 0
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(_READ)
            if not chunk:
                break
            total += len(chunk)
            sha.update(chunk)
            whole.update(chunk)
            view = memoryview(chunk)
            while view:
                take = min(len(view), part_size - in_part)
                part.update(view[:take])
                in_part += take
                view = view[take:]
                if in_part == part_size:
                    parts.append(part.digest())
                    part = hashlib.md5()
                    in_part = 0
    if in_part:
        parts.append(part.digest())
    return FileHashes(
        size=total,
        sha256=sha.hexdigest(),
        md5=whole.hexdigest(),
        part_size=part_size,
        part_md5s=tuple(parts),
    )


def normalize_etag(etag: str) -> str:
    return str(etag or "").strip().strip('"').lower()


def etag_matches(local: FileHashes, remote_etag: str) -> tuple[bool, str]:
    """(ok, detail) comparing a stored ETag with the local file, whichever upload mode was used."""
    remote = normalize_etag(remote_etag)
    if "-" in remote:
        try:
            n = int(remote.rsplit("-", 1)[1])
        except ValueError:
            return False, f"unparseable ETag {remote!r}"
        if n != local.parts:
            return False, f"multipart ETag has {n} parts, expected {local.parts} at {local.part_size} bytes"
        ok = remote == local.multipart_etag()
        return ok, "multipart ETag match" if ok else "multipart ETag mismatch"
    ok = remote == local.md5
    return ok, "MD5 ETag match" if ok else "MD5 ETag mismatch"
