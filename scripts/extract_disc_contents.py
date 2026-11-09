#!/usr/bin/env python3
"""Extract a data disc's files and make web copies (disc-contents image: no network, ready/ read-only).

    extract_disc_contents.py --package /data/intake/ready/<slug> --out /output/<slug>

Writes into --out:
  files/<volume label>/...   the disc's files, original names and dates
  derived/<path>.<suffix>    web copies: .mp4 + .sheet.jpg + .poster.jpg per video, .pdf per
                             Word/RTF/WRI/Excel/PowerPoint file, .page1.jpg per PDF, .jpg per
                             BMP/PCX/TGA/TIFF image
  listing.json               every file: path, size, mtime, sha256, mime, kind, derived copies
  result.json                status (ok | no_image | failed), reason, tool versions

The disc image is never changed: raw CD sectors (BIN, MODE1/2352 or MODE2/2352) are converted
into a scratch ISO first. Programs are listed, never run. Nothing here decides whether the
contents are shown; that's build_disc_contents_bag.py (scripts/disc_contents_policy.py).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageStat

SECTOR = 2048
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

IMAGE_NATIVE = {"jpg", "jpeg", "png", "gif", "webp"}
IMAGE_CONVERT = {"bmp", "pcx", "tga", "tif", "tiff"}
VIDEO = {"mpg", "mpeg", "mpe", "m1v", "m2v", "mp4", "m4v", "mov", "qt", "avi", "wmv", "asf", "flv",
         "webm", "vob", "smk", "bik"}
AUDIO = {"mp3", "wav", "aif", "aiff", "au", "snd", "wma", "ogg", "flac"}
DOC_CONVERT = {"doc", "docx", "rtf", "wri", "xls", "xlsx", "ppt", "pptx", "pps"}
DOC_PDF = {"pdf"}
PROGRAMS = {"exe", "com", "bat", "cmd", "dll", "sys", "drv", "vxd", "scr", "cpl", "ocx", "msi", "pif",
            "lnk", "hqx", "sit", "sea", "app", "jar"}


def kind_of(name: str) -> str:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in IMAGE_NATIVE or ext in IMAGE_CONVERT:
        return "image"
    if ext in VIDEO:
        return "video"
    if ext in AUDIO:
        return "audio"
    if ext in DOC_CONVERT or ext in DOC_PDF:
        return "document"
    if ext in PROGRAMS:
        return "program"
    return "other"


# --- disc image ---------------------------------------------------------------------------

_CUE_FILE = re.compile(r'^\s*FILE\s+"?(.+?)"?\s+\w+\s*$', re.IGNORECASE)
_CUE_TRACK = re.compile(r"^\s*TRACK\s+(\d+)\s+(\S+)", re.IGNORECASE)


def data_track(package: Path) -> tuple[Path, str] | None:
    """(image file, mode) of the disc's first data track: an .iso, or the first data TRACK of a .cue.

    Prefers a cue whose data track file exists: a dumper who renamed the image to its Redump name
    may have left the old cue, pointing at a file that's gone, next to the new one.
    """
    isos = sorted(p for p in package.iterdir() if p.suffix.lower() == ".iso")
    if isos:
        return isos[0], "MODE1/2048"
    found: list[tuple[Path, str]] = []
    for cue in sorted(p for p in package.iterdir() if p.suffix.lower() == ".cue"):
        current = None
        for line in cue.read_text(encoding="utf-8", errors="replace").splitlines():
            f = _CUE_FILE.match(line)
            if f:
                current = package / Path(f.group(1).replace("\\", "/")).name
                continue
            t = _CUE_TRACK.match(line)
            if t and current is not None and t.group(2).upper() != "AUDIO":
                found.append((current, t.group(2).upper()))
                break
    return next((track for track in found if track[0].is_file()), found[0] if found else None)


def to_iso(image: Path, mode: str, dest: Path) -> Path:
    """User data of every sector, as a plain 2048-byte-sector ISO."""
    if mode == "MODE1/2048":
        return image
    if mode == "MODE1/2352":
        offset = 16
    elif mode == "MODE2/2352":
        offset = 24  # CD-ROM XA Form 1: 8-byte subheader after sync + header
    else:
        raise ValueError(f"unsupported track mode {mode}")
    with image.open("rb") as src, dest.open("wb") as out:
        while sector := src.read(2352):
            out.write(sector[offset:offset + SECTOR])
    return dest


def volume_label(iso: Path) -> str | None:
    """ISO 9660 Primary Volume Descriptor volume identifier (sector 16, bytes 40-71)."""
    with iso.open("rb") as f:
        f.seek(16 * SECTOR)
        pvd = f.read(SECTOR)
    if pvd[1:6] != b"CD001":
        return None
    label = pvd[40:72].decode("ascii", errors="replace").strip(" \x00")
    return label or None


def safe_name(name: str | None) -> str:
    name = (name or "").strip()
    if not name or name in (".", "..") or any(c in name for c in "/\\\x00\r\n"):
        return "disc"
    return name


def listed_total(iso: Path) -> int:
    res = subprocess.run(["7z", "l", "-slt", str(iso)], capture_output=True, text=True, check=False)
    total, is_dir = 0, False
    for line in (res.stdout or "").splitlines():
        if line.startswith("Folder = "):
            is_dir = line.endswith("+")
        elif line.startswith("Size = ") and not is_dir:
            total += int(line.split("=", 1)[1] or 0)
    return total


def extract(iso: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    # 7z exits 2 on UDF/ISO "Headers Error" warnings while extracting everything; check the result.
    res = subprocess.run(["7z", "x", "-y", f"-o{dest}", str(iso)], capture_output=True, text=True, check=False)
    if not any(p.is_file() for p in dest.rglob("*")):
        raise RuntimeError(f"7z extracted nothing (exit {res.returncode}): {res.stderr[-500:]}")


# --- web copies ---------------------------------------------------------------------------

def _run(cmd: list[str], timeout: int = 1800, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=timeout, **kw)


def video_duration(path: Path) -> float | None:
    res = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], 120)
    try:
        return float(res.stdout.strip())
    except ValueError:
        return None


def video_mp4(src: Path, dest: Path) -> bool:
    res = _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
                "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "medium", "-crf", "23",
                "-pix_fmt", "yuv420p", "-vf", "scale=trunc(iw*sar/2)*2:trunc(ih/2)*2,setsar=1",
                "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(dest)])
    return res.returncode == 0 and dest.is_file() and dest.stat().st_size > 0


def timecode(sec: float) -> str:
    s = int(sec)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def contact_sheet(src: Path, sheet: Path, poster: Path, work: Path, *, frames: int = 12, columns: int = 4,
                  tile_width: int = 320) -> bool:
    """frames stills spread over the video (first and last included), 4 columns with timecodes; the
    poster is the brightest of the first few."""
    duration = video_duration(src) or 0.0
    if duration <= 0:
        return False
    n = max(1, min(frames, int(duration)))
    end = max(0.0, duration - 0.5)  # a seek to the very end often decodes nothing
    times = [0.0] if n == 1 else [min(end, k * duration / (n - 1)) for k in range(n)]
    stills = []
    for i, t in enumerate(times):
        out = work / f"still_{i:02d}.jpg"
        for at in (t, max(0.0, t - 1.0)):  # retry a second earlier if nothing decoded
            _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{max(0.0, at):.2f}", "-i", str(src),
                  "-frames:v", "1", "-vf", "scale=trunc(iw*sar/2)*2:ih,setsar=1", str(out)], 120)
            if out.is_file() and out.stat().st_size:
                stills.append((out, at))
                break
    if not stills:
        return False
    tiles = []
    for path, t in stills:
        with Image.open(path) as im:
            im = im.convert("RGB")
            tiles.append((im.resize((tile_width, max(1, round(im.height * tile_width / im.width))), Image.LANCZOS), t))
    tile_h = max(t.height for t, _ in tiles)
    rows = -(-len(tiles) // columns)
    canvas = Image.new("RGB", (tile_width * min(columns, len(tiles)), tile_h * rows), "black")
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype(FONT, 15)
    except OSError:
        font = ImageFont.load_default()
    for i, (tile, t) in enumerate(tiles):
        x, y = (i % columns) * tile_width, (i // columns) * tile_h
        canvas.paste(tile, (x, y + (tile_h - tile.height) // 2))
        label = timecode(t)
        l, tp, r, b = draw.textbbox((0, 0), label, font=font)
        bx, by = x + 5, y + tile_h - (b - tp) - 11
        draw.rectangle((bx, by, bx + (r - l) + 8, by + (b - tp) + 6), fill=(0, 0, 0))
        draw.text((bx + 4 - l, by + 3 - tp), label, fill="white", font=font)
    sheet.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(sheet, "JPEG", quality=85, optimize=True)

    def brightness(p: Path) -> float:
        with Image.open(p) as im:
            return ImageStat.Stat(im.convert("L")).mean[0]

    best = max(stills[: min(4, len(stills))], key=lambda s: brightness(s[0]))[0]
    shutil.copyfile(best, poster)
    return True


def office_pdf(src: Path, dest: Path, work: Path) -> bool:
    out_dir = work / "soffice"
    out_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, HOME=str(work))
    _run(["soffice", "--headless", "--norestore", "--convert-to", "pdf", "--outdir", str(out_dir), str(src)], 300,
         env=env)
    made = out_dir / (src.stem + ".pdf")
    if not made.is_file() or made.stat().st_size == 0:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(made), dest)
    return True


def pdf_page1(pdf: Path, dest: Path) -> bool:
    stem = dest.with_suffix("")
    _run(["pdftoppm", "-jpeg", "-f", "1", "-l", "1", "-singlefile", "-scale-to", "640", str(pdf), str(stem)], 120)
    return dest.is_file() and dest.stat().st_size > 0


def image_jpeg(src: Path, dest: Path, max_edge: int = 2400) -> bool:
    try:
        with Image.open(src) as im:
            im = im.convert("RGB")
            im.thumbnail((max_edge, max_edge))
            dest.parent.mkdir(parents=True, exist_ok=True)
            im.save(dest, "JPEG", quality=88, optimize=True)
        return True
    except (OSError, ValueError):
        return False


def mime_of(path: Path) -> str | None:
    res = _run(["file", "--brief", "--mime-type", str(path)], 60)
    return res.stdout.strip() or None


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def make_web_copies(rel: str, src: Path, derived_root: Path, work: Path) -> dict[str, str]:
    """Web copies for one file; {role: path relative to --out}."""
    ext = src.suffix.lower().lstrip(".")
    kind = kind_of(src.name)
    out: dict[str, str] = {}
    base = derived_root / rel

    def keep(role: str, path: Path) -> None:
        out[role] = path.relative_to(derived_root.parent).as_posix()

    base.parent.mkdir(parents=True, exist_ok=True)
    if kind == "video":
        mp4 = Path(f"{base}.mp4")
        if video_mp4(src, mp4):
            keep("access", mp4)
        sheet, poster = Path(f"{base}.sheet.jpg"), Path(f"{base}.poster.jpg")
        with tempfile.TemporaryDirectory(dir=work) as tmp:
            if contact_sheet(src, sheet, poster, Path(tmp)):
                keep("sheet", sheet)
                keep("poster", poster)
    elif kind == "document":
        pdf = src if ext in DOC_PDF else Path(f"{base}.pdf")
        if ext not in DOC_PDF:
            with tempfile.TemporaryDirectory(dir=work) as tmp:
                if office_pdf(src, pdf, Path(tmp)):
                    keep("access", pdf)
                else:
                    pdf = None
        if pdf is not None:
            page1 = Path(f"{base}.page1.jpg")
            if pdf_page1(pdf, page1):
                keep("page1", page1)
    elif kind == "image" and ext in IMAGE_CONVERT:
        jpg = Path(f"{base}.jpg")
        if image_jpeg(src, jpg):
            keep("access", jpg)
    return out


def tool_versions() -> dict[str, str]:
    out = {}
    for name, cmd in (("ffmpeg", ["ffmpeg", "-version"]), ("libreoffice", ["soffice", "--version"]),
                      ("7z", ["7z"]), ("pdftoppm", ["pdftoppm", "-v"])):
        try:
            res = _run(cmd, 60)
            text = (res.stdout or res.stderr).strip().splitlines()
            out[name] = next((ln for ln in text if ln.strip()), "")[:120]
        except (OSError, subprocess.TimeoutExpired):
            out[name] = "unavailable"
    return out


# Encode labels at the end of a video's name: the same clip in several sizes (press asset discs
# often carry _BIG/_HD_720p/_HI/_LO of each). Higher rank = better web copy source.
_VARIANT = re.compile(r"[\s_-]+(?P<v>hd[\s_-]?1080[pi]?|1080[pi]|hd[\s_-]?720p?|720p|hd|big|large|high|hi|"
                      r"med(?:ium)?|sd|small|low|lo)$", re.IGNORECASE)


def _variant_rank(label: str) -> int:
    v = re.sub(r"[\s_-]", "", label.lower())
    if "1080" in v:
        return 4
    if "720" in v or v == "hd":
        return 3
    if v in ("big", "large", "high", "hi"):
        return 2
    if v in ("med", "medium"):
        return 1
    return 0


def video_variant_skips(sizes: dict[str, int]) -> set[str]:
    """Videos to skip for web copies: when one folder holds the same clip in several encodes
    (name + encode label), only the best one (resolution label, then size) gets an MP4. The others
    stay in the listing and the bag for download."""
    groups: dict[tuple[str, str], list[tuple[int, int, str]]] = {}
    for rel, size in sizes.items():
        folder, _, name = rel.rpartition("/")
        stem = name.rsplit(".", 1)[0]
        m = _VARIANT.search(stem)
        if not m or not stem[:m.start()]:
            continue
        groups.setdefault((folder, stem[:m.start()].casefold()), []).append((_variant_rank(m["v"]), size, rel))
    skips: set[str] = set()
    for members in groups.values():
        if len(members) > 1:
            best = max(members)
            skips.update(rel for *_, rel in members if rel != best[2])
    return skips


def run(package: Path, out: Path, *, web_copies: bool = True) -> dict:
    for stale in ("files", "derived"):
        shutil.rmtree(out / stale, ignore_errors=True)
    for stale in ("listing.json", "result.json"):
        (out / stale).unlink(missing_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    result: dict = {"package": str(package), "status": None,
                    "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat()}
    track = data_track(package) if package.is_dir() else None
    if track is None:
        result.update(status="no_image", reason="no .iso or data track in a .cue")
        return _finish(out, result)
    image, mode = track
    result.update(image=image.name, track_mode=mode)
    with tempfile.TemporaryDirectory(dir=out, prefix=".work-") as work_s:
        work = Path(work_s)
        try:
            iso = to_iso(image, mode, work / "disc.iso")
        except ValueError as exc:
            result.update(status="failed", reason=str(exc))
            return _finish(out, result)
        label = volume_label(iso)
        root = safe_name(label)
        result.update(volume_label=label, root=root)
        total = listed_total(iso)
        result["listed_bytes"] = total
        files_root = out / "files" / root
        try:
            extract(iso, files_root)
        except RuntimeError as exc:
            result.update(status="failed", reason=str(exc))
            return _finish(out, result)
        derived_root = out / "derived"
        entries = []
        all_files = sorted(p for p in files_root.rglob("*") if p.is_file())
        skip_videos = video_variant_skips({p.relative_to(out / "files").as_posix(): p.stat().st_size
                                           for p in all_files if kind_of(p.name) == "video"})
        for path in all_files:
            rel = path.relative_to(out / "files").as_posix()
            entry = {
                "path": rel,
                "size": path.stat().st_size,
                "mtime": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).replace(microsecond=0).isoformat(),
                "sha256": sha256_of(path),
                "mime": mime_of(path),
                "kind": kind_of(path.name),
            }
            if rel in skip_videos:
                entry["derived"] = {}
                entry["web_copy_skipped"] = "another encode of the same clip has the web copy"
            elif web_copies:
                try:
                    entry["derived"] = make_web_copies(rel, path, derived_root, work)
                except (OSError, subprocess.TimeoutExpired) as exc:
                    entry["derived"] = {}
                    entry["error"] = f"{type(exc).__name__}: {exc}"[:300]
            entries.append(entry)
            print(f"  {entry['kind']:8s} {rel} {' '.join(entry.get('derived', {}))}", flush=True)
    kinds: dict[str, dict[str, int]] = {}
    for e in entries:
        k = kinds.setdefault(e["kind"], {"files": 0, "bytes": 0})
        k["files"] += 1
        k["bytes"] += e["size"]
    listing = {"volume_label": label, "root": root, "image": image.name, "track_mode": mode,
               "total_files": len(entries), "total_bytes": sum(e["size"] for e in entries),
               "by_kind": kinds, "files": entries}
    (out / "listing.json").write_text(json.dumps(listing, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    result.update(status="ok", total_files=len(entries), by_kind=kinds, tools=tool_versions())
    return _finish(out, result)


def _finish(out: Path, result: dict) -> dict:
    (out / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"disc-contents {Path(result['package']).name}: {result['status']}"
          + (f" ({result['reason']})" if result.get("reason") else ""), flush=True)
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--package", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    result = run(args.package, args.out)
    return 0 if result["status"] in ("ok", "no_image") else 1


if __name__ == "__main__":
    sys.exit(main())
