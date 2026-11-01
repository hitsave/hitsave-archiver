"""Read file extents (byte ranges in the image) from a UDF filesystem: UDF 1.02-2.60, including
the 2.50+ Metadata Partition used by Blu-ray. Used by clamav_full_scan to scan UDF-only images
file by file (plus the bytes between files) without extracting them.

ECMA-167 / OSTA UDF structures: AVDP (sector 256) -> Volume Descriptor Sequence (Partition
Descriptor, Logical Volume Descriptor with partition maps and the File Set Descriptor location)
-> File Set Descriptor -> root directory ICB -> File (Identifier) Descriptors -> File Entries.
"""
from __future__ import annotations

import struct

TAG_PD, TAG_LVD, TAG_TD = 5, 6, 8
TAG_FSD, TAG_FID, TAG_AED, TAG_FE, TAG_EFE = 256, 257, 258, 261, 266


class UdfError(ValueError):
    pass


def _tag(buf: bytes) -> int:
    return struct.unpack_from("<H", buf, 0)[0] if len(buf) >= 16 else -1


class _Volume:
    def __init__(self, fh, size: int, sector: int = 2048):
        self.fh, self.size, self.sector = fh, size, sector
        self.partitions: dict[int, int] = {}  # partition number -> start sector
        self.maps: list[dict] = []  # partition reference -> map
        self.block = sector
        self.meta_extents: list[tuple[int, int]] | None = None  # metadata file: (phys_lbn, blocks)

    def read(self, byte_off: int, n: int) -> bytes:
        if byte_off < 0 or byte_off + n > self.size:
            raise UdfError(f"read outside image at {byte_off}+{n}")
        self.fh.seek(byte_off)
        return self.fh.read(n)

    # --- address translation ---------------------------------------------------------------
    def phys_byte(self, partref: int, lbn: int) -> int:
        m = self.maps[partref]
        if m["type"] == 1:
            return (self.partitions[m["partition"]] + lbn) * self.sector
        # Metadata partition: logical blocks map through the metadata file's extents.
        if self.meta_extents is None:
            raise UdfError("metadata partition without metadata file")
        remaining = lbn
        for phys_lbn, blocks in self.meta_extents:
            if remaining < blocks:
                return self.phys_byte(m["phys_ref"], phys_lbn + remaining)
            remaining -= blocks
        raise UdfError(f"metadata block {lbn} beyond metadata file")



def _volume_descriptors(vol: _Volume) -> None:
    avdp = vol.read(256 * vol.sector, vol.sector)
    if _tag(avdp) != 2:
        raise UdfError("no Anchor Volume Descriptor Pointer at sector 256")
    vds_len, vds_loc = struct.unpack_from("<II", avdp, 16)
    lvd = None
    for i in range(max(1, vds_len // vol.sector)):
        d = vol.read((vds_loc + i) * vol.sector, vol.sector)
        t = _tag(d)
        if t == TAG_PD:
            number = struct.unpack_from("<H", d, 22)[0]
            vol.partitions[number] = struct.unpack_from("<I", d, 188)[0]
        elif t == TAG_LVD:
            lvd = d
        elif t == TAG_TD:
            break
    if lvd is None or not vol.partitions:
        raise UdfError("Partition / Logical Volume Descriptor missing")
    vol.block = struct.unpack_from("<I", lvd, 212)[0]
    if vol.block != vol.sector:
        raise UdfError(f"unsupported logical block size {vol.block}")
    vol.fsd = (struct.unpack_from("<I", lvd, 252)[0], struct.unpack_from("<H", lvd, 256)[0])
    n_maps = struct.unpack_from("<I", lvd, 268)[0]
    off = 440
    for _ in range(n_maps):
        mtype, mlen = lvd[off], lvd[off + 1]
        if mtype == 1:
            vol.maps.append({"type": 1, "partition": struct.unpack_from("<H", lvd, off + 4)[0]})
        elif mtype == 2 and b"*UDF Metadata Partition" in lvd[off + 4 : off + 36]:
            part = struct.unpack_from("<H", lvd, off + 38)[0]
            vol.maps.append({"type": 2, "partition": part,
                             "meta_file_lbn": struct.unpack_from("<I", lvd, off + 40)[0]})
        else:
            raise UdfError(f"unsupported partition map type {mtype}")
        off += mlen
    for m in vol.maps:  # metadata maps point at the type-1 map of the same partition
        if m["type"] == 2:
            m["phys_ref"] = next(i for i, x in enumerate(vol.maps)
                                 if x["type"] == 1 and x["partition"] == m["partition"])
            fe = vol.read(vol.phys_byte(m["phys_ref"], m["meta_file_lbn"]), vol.sector)
            vol.meta_extents = [(lbn, -(-length // vol.sector))
                                for kind, length, lbn, _ref in _allocs(vol, fe, m["phys_ref"])
                                if kind == 0]


def _allocs(vol: _Volume, fe: bytes, partref: int):
    """Yield (extent_type, length_bytes, lbn, partref) for an (Extended) File Entry."""
    t = _tag(fe)
    if t == TAG_FE:
        l_ea, l_ad = struct.unpack_from("<II", fe, 168)
        start = 176 + l_ea
    elif t == TAG_EFE:
        l_ea, l_ad = struct.unpack_from("<II", fe, 208)
        start = 216 + l_ea
    else:
        raise UdfError(f"expected File Entry, got tag {t}")
    ad_type = struct.unpack_from("<H", fe, 34)[0] & 7
    if ad_type == 3:  # data embedded in the File Entry itself
        return
    buf, pos, end = fe, start, start + l_ad
    guard = 0
    while pos < end and guard < 100_000:
        guard += 1
        if ad_type == 0:
            raw_len, lbn = struct.unpack_from("<II", buf, pos)
            ref = partref  # ECMA-167: short_ad refers to the ICB's own partition
            pos += 8
        elif ad_type == 1:
            raw_len, lbn, ref = struct.unpack_from("<IIH", buf, pos)
            pos += 16
        else:
            raise UdfError(f"unsupported allocation descriptor type {ad_type}")
        kind, length = raw_len >> 30, raw_len & 0x3FFFFFFF
        if length == 0:
            break
        if kind == 3:  # continuation: next block of allocation descriptors (AED)
            buf = vol.read(vol.phys_byte(ref, lbn), vol.sector)
            if _tag(buf) != TAG_AED:
                raise UdfError("bad Allocation Extent Descriptor")
            pos, end = 24, 24 + struct.unpack_from("<I", buf, 20)[0]
            continue
        yield kind, length, lbn, ref


def _file_entry(vol: _Volume, lbn: int, partref: int) -> tuple[bytes, int]:
    fe = vol.read(vol.phys_byte(partref, lbn), vol.sector)
    if _tag(fe) not in (TAG_FE, TAG_EFE):
        raise UdfError(f"no File Entry at {partref}:{lbn}")
    file_type = fe[27]
    return fe, file_type


def _read_extents(vol: _Volume, fe: bytes, partref: int) -> bytes:
    t = _tag(fe)
    info_len = struct.unpack_from("<Q", fe, 56)[0]
    if struct.unpack_from("<H", fe, 34)[0] & 7 == 3:
        l_ea, l_ad = struct.unpack_from("<II", fe, 168 if t == TAG_FE else 208)
        start = (176 if t == TAG_FE else 216) + l_ea
        return fe[start : start + l_ad][:info_len]
    out = b""
    for kind, length, lbn, ref in _allocs(vol, fe, partref):
        if kind == 0:
            out += vol.read(vol.phys_byte(ref, lbn), length)
    return out[:info_len]


def _decode_name(raw: bytes) -> str:
    if not raw:
        return ""
    if raw[0] == 8:
        return raw[1:].decode("latin-1")
    if raw[0] == 16:
        return raw[1:].decode("utf-16-be", errors="replace")
    return raw.decode("latin-1", errors="replace")


def udf_extents(fh, size: int) -> list[tuple[int, int, str]] | None:
    """[(byte_start, byte_end, path)] for every recorded file extent, or None if not UDF."""
    vol = _Volume(fh, size)
    try:
        _volume_descriptors(vol)
    except (UdfError, struct.error):
        return None
    fsd = vol.read(vol.phys_byte(vol.fsd[1], vol.fsd[0]), vol.sector)
    if _tag(fsd) != TAG_FSD:
        raise UdfError("File Set Descriptor not found")
    root_lbn, root_ref = struct.unpack_from("<I", fsd, 404)[0], struct.unpack_from("<H", fsd, 408)[0]
    out: list[tuple[int, int, str]] = []
    seen: set[tuple[int, int]] = set()
    stack = [(root_lbn, root_ref, "")]
    while stack:
        lbn, ref, prefix = stack.pop()
        if (lbn, ref) in seen or len(seen) > 1_000_000:
            continue
        seen.add((lbn, ref))
        fe, _ = _file_entry(vol, lbn, ref)
        data = _read_extents(vol, fe, ref)
        pos = 0
        while pos + 38 <= len(data):
            if _tag(data[pos:]) != TAG_FID:
                break
            chars, l_fi = data[pos + 18], data[pos + 19]
            icb_lbn, icb_ref = struct.unpack_from("<IH", data, pos + 24)
            l_iu = struct.unpack_from("<H", data, pos + 36)[0]
            name = _decode_name(data[pos + 38 + l_iu : pos + 38 + l_iu + l_fi])
            pos += (38 + l_iu + l_fi + 3) & ~3
            if chars & 0x08 or chars & 0x04:  # parent / deleted
                continue
            path = f"{prefix}/{name}".lstrip("/")
            if chars & 0x02:
                stack.append((icb_lbn, icb_ref, path))
                continue
            child, _ = _file_entry(vol, icb_lbn, icb_ref)
            for kind, length, ext_lbn, ext_ref in _allocs(vol, child, icb_ref):
                if kind == 0 and length:
                    start = vol.phys_byte(ext_ref, ext_lbn)
                    out.append((start, min(start + length, size), path))
    return sorted(out)
