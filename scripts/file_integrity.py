"""Damaged-file checks for payload files: images, PDFs, video/audio and archives.

A check never blocks preservation. Every file is preserved as received; a damaged one is
recorded as damaged, so it can be replaced if an intact copy turns up later.

Statuses:
  ok         the file opened and decoded (or tested) without errors
  damaged    the tool reported the file as truncated, corrupt or not the format its name says
  unchecked  the check could not give an answer (password, too large, tool failed or timed out)

Files of other kinds (disc images, text, executables) get no result: dumps are verified
against their Redump/No-Intro hashes instead.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

IMAGE_EXT = {".jpg", ".jpeg", ".jpe", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".tga", ".pcx"}
PDF_EXT = {".pdf"}
MEDIA_EXT = {
    ".mp4", ".m4v", ".mov", ".avi", ".mpg", ".mpeg", ".m2v", ".wmv", ".flv", ".mkv", ".webm", ".3gp",
    ".mp3", ".wav", ".aif", ".aiff", ".ogg", ".wma", ".m4a", ".flac",
}
ARCHIVE_EXT = {".zip", ".7z", ".rar"}
TIFF_EXT = {".tif", ".tiff"}

# Larger images are not decoded: 500 MP of RGBA is 2 GB of RAM.
MAX_IMAGE_PIXELS = 500_000_000
TIMEOUT_SECONDS = 900
# A full decode of a local video or audio file at ingest; long videos take minutes.
FULL_DECODE_TIMEOUT_SECONDS = 3600
PNG_TEXT_LIMIT = 256 * 1024 * 1024
DETAIL_MAX = 300


@dataclass(frozen=True)
class IntegrityResult:
    status: str
    check: str | None = None
    detail: str | None = None

    def record(self) -> dict:
        out = {"status": self.status}
        if self.check:
            out["check"] = self.check
        if self.detail:
            out["detail"] = self.detail
        return out


def kind_for(path: Path) -> str | None:
    ext = path.suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in PDF_EXT:
        return "pdf"
    if ext in MEDIA_EXT:
        return "media"
    if ext in ARCHIVE_EXT:
        return "archive"
    return None


def _short(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    joined = "; ".join(dict.fromkeys(lines))  # keep order, drop repeats
    return joined[:DETAIL_MAX] if joined else "no detail"


def check_image(path: Path) -> IntegrityResult:
    if Path(path).suffix.lower() in TIFF_EXT and shutil.which("tiffinfo"):
        return check_tiff(path)
    from PIL import Image, ImageFile, PngImagePlugin, UnidentifiedImageError

    # dip_package_previews sets LOAD_TRUNCATED_IMAGES = True process-wide (so previews of
    # cut-off scans still render). This check must see truncation, so it decodes strictly
    # and puts both globals back afterwards.
    # Pillow also refuses PNG text chunks (XMP, ICC) over 1 MB as a safety limit; a large
    # metadata chunk isn't damage, so those limits are raised for the check too.
    saved = (ImageFile.LOAD_TRUNCATED_IMAGES, Image.MAX_IMAGE_PIXELS,
             PngImagePlugin.MAX_TEXT_CHUNK, PngImagePlugin.MAX_TEXT_MEMORY)
    try:
        ImageFile.LOAD_TRUNCATED_IMAGES = False
        Image.MAX_IMAGE_PIXELS = None  # size is capped below instead
        PngImagePlugin.MAX_TEXT_CHUNK = PNG_TEXT_LIMIT
        PngImagePlugin.MAX_TEXT_MEMORY = PNG_TEXT_LIMIT
        with Image.open(path) as img:
            pixels = img.width * img.height
            if pixels > MAX_IMAGE_PIXELS:
                return IntegrityResult("unchecked", "pillow", f"{pixels} pixels is over the {MAX_IMAGE_PIXELS} limit")
            img.verify()  # structure and chunk CRCs (PNG)
        with Image.open(path) as img:
            img.load()  # full decode: catches truncated data
    except UnidentifiedImageError:
        # Pillow lacks some valid variants (CMYK+alpha TIFF, for one); let ffmpeg decide.
        return _image_via_ffmpeg(path)
    except (OSError, SyntaxError, ValueError, EOFError) as exc:
        return IntegrityResult("damaged", "pillow", _short(str(exc)))
    finally:
        (ImageFile.LOAD_TRUNCATED_IMAGES, Image.MAX_IMAGE_PIXELS,
         PngImagePlugin.MAX_TEXT_CHUNK, PngImagePlugin.MAX_TEXT_MEMORY) = saved
    return IntegrityResult("ok", "pillow")


def check_tiff(path: Path) -> IntegrityResult:
    """Decode every strip or tile of every page with libtiff, the reference TIFF library.

    Pillow has no mode for some valid TIFFs (CMYK+alpha) and doesn't notice some broken
    compressed data. libtiff reads one strip at a time, so a huge scan needs no pixel cap.
    tiffinfo -D exits 1 on a read or decode error and 0 otherwise (warnings about unknown
    tags are not damage).
    """
    proc = _run(["tiffinfo", "-D", str(path)], path)
    if proc.returncode == 0:
        return IntegrityResult("ok", "libtiff")
    errors = [line for line in proc.stderr.splitlines() if "warning" not in line.lower()]
    return IntegrityResult("damaged", "libtiff", _short("\n".join(errors) or proc.stderr))


def _display_name(target: Path | str) -> str:
    text = str(target)
    if "://" in text:
        from urllib.parse import unquote, urlparse
        return Path(unquote(urlparse(text).path)).name
    return Path(text).name


def _image_via_ffmpeg(path: Path) -> IntegrityResult:
    """Second opinion for an image Pillow can't identify: a full ffmpeg decode."""
    if shutil.which("ffmpeg") is None:
        return IntegrityResult("unchecked", "pillow", "Pillow can't read this image variant; ffmpeg missing")
    proc = _run(["ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-f", "null", "-"], path)
    if proc.returncode == 0:
        return IntegrityResult("ok", "ffmpeg", "Pillow can't read this image variant; ffmpeg decoded it")
    return IntegrityResult("damaged", "pillow", "not a readable image (unknown or corrupt header)")


def _run(cmd: list[str], target: Path | str, *, timeout: int = TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    """Run a checker. Tools quote the path they read (a temp file, or a signed URL in the
    backfill); that is replaced by the bare file name before anything is recorded."""
    proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout, check=False)
    quoted, name = str(target), _display_name(target)
    proc.stdout = (proc.stdout or "").replace(f"{quoted}: ", "").replace(quoted, name)
    proc.stderr = (proc.stderr or "").replace(f"{quoted}: ", "").replace(quoted, name)
    return proc


# qpdf warnings that mean content is missing (a cut-off or corrupt file), not just a bad
# cross-reference table that qpdf rebuilt (common in old PDFs that display fine).
PDF_LOSS_MARKERS = (
    "unable to find trailer",
    "unable to recover stream",
    "treating stream as empty",
    "eof after",
    "eof while",
    "unexpected eof",
)


def check_pdf(path: Path) -> IntegrityResult:
    # qpdf --check: 0 = clean, 3 = warnings only (qpdf recovered), 2 = errors.
    proc = _run(["qpdf", "--check", str(path)], path)
    output = proc.stderr + proc.stdout
    if "invalid password" in output.lower():
        return IntegrityResult("unchecked", "qpdf", "password-protected")
    if proc.returncode == 0:
        return IntegrityResult("ok", "qpdf")
    if proc.returncode == 3:
        if any(marker in proc.stderr.lower() for marker in PDF_LOSS_MARKERS):
            return IntegrityResult("damaged", "qpdf", _short(proc.stderr))
        return IntegrityResult("ok", "qpdf", "recoverable: " + _short(proc.stderr))
    return IntegrityResult("damaged", "qpdf", _short(proc.stderr or proc.stdout))


def check_media(path: Path | str, *, full: bool = False) -> IntegrityResult:
    """*path* may be an https URL (a presigned Wasabi link): ffmpeg reads only what it needs.

    full: decode the whole file (local files at ingest), which also finds damage in the middle.
    Otherwise only the last 15 seconds are decoded, which finds a cut-off file cheaply.
    """
    probe = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], path)
    duration = (probe.stdout or "").strip()
    if probe.returncode != 0 or not duration or duration == "N/A":
        return IntegrityResult("damaged", "ffprobe", _short(probe.stderr) if probe.stderr.strip() else "no duration")
    if full:
        whole = _run(["ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-f", "null", "-"], path,
                     timeout=FULL_DECODE_TIMEOUT_SECONDS)
        if whole.returncode != 0:
            return IntegrityResult("damaged", "ffmpeg", _short(whole.stderr))
        return IntegrityResult("ok", "ffmpeg")
    # Decode the last 15 seconds: a cut-off file fails here even when its header is intact.
    tail = _run(["ffmpeg", "-v", "error", "-xerror", "-sseof", "-15", "-i", str(path), "-f", "null", "-"], path)
    if tail.returncode == 0:
        return IntegrityResult("ok", "ffprobe")
    if _first_error_is_mpeg_audio(tail.stderr) and _has_video(path):
        # The seek lands inside an MP1/MP2/MP3 audio frame, which fails on intact files too (and a
        # cut that hits only the audio looks the same), so judge the end of the file by its video.
        video = _run(["ffmpeg", "-v", "error", "-xerror", "-sseof", "-15", "-i", str(path), "-map", "0:v",
                      "-f", "null", "-"], path)
        if video.returncode == 0:
            return IntegrityResult("ok", "ffmpeg", "video checked; MPEG audio can't be checked after a seek")
        return IntegrityResult("damaged", "ffmpeg", _short(video.stderr))
    return IntegrityResult("damaged", "ffmpeg", _short(tail.stderr))


_MPEG_AUDIO_DECODER = re.compile(r"\b(?:dec:)?mp[123](?:float|fixed)?\s*@")


def _first_error_is_mpeg_audio(stderr: str) -> bool:
    """-xerror stops at the first decoding error; the lines after it are the shutdown."""
    lines = [line for line in (stderr or "").splitlines() if line.strip()]
    return bool(lines) and bool(_MPEG_AUDIO_DECODER.search(lines[0]))


def _has_video(path: Path | str) -> bool:
    probe = _run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=codec_type",
                  "-of", "csv=p=0", str(path)], path)
    return "video" in (probe.stdout or "")


def _seven_zip() -> str:
    return shutil.which("7zz") or shutil.which("7z") or "7zz"


def check_archive(path: Path) -> IntegrityResult:
    # A fixed dummy password stops 7-Zip prompting; encrypted archives then report it.
    proc = _run([_seven_zip(), "t", "-bd", "-pHITSAVE-NO-PASSWORD", str(path)], path)
    output = proc.stdout + proc.stderr
    if "Wrong password" in output or ("encrypted" in output.lower() and proc.returncode != 0):
        return IntegrityResult("unchecked", "7z", "password-protected")
    if proc.returncode == 0:
        return IntegrityResult("ok", "7z")
    errors = [line for line in output.splitlines() if "ERROR" in line.upper() or "Unexpected" in line]
    return IntegrityResult("damaged", "7z", _short("\n".join(errors) or output))


def missing_tools() -> list[str]:
    """External programs the checks need that aren't on PATH."""
    needed = ["qpdf", "ffprobe", "ffmpeg", "tiffinfo"]
    missing = [tool for tool in needed if shutil.which(tool) is None]
    if shutil.which("7zz") is None and shutil.which("7z") is None:
        missing.append("7zz")
    return missing


CHECKERS = {"image": check_image, "pdf": check_pdf, "media": check_media, "archive": check_archive}


def check_file(path: Path, *, source: str | None = None) -> IntegrityResult | None:
    """Check one file; None when there's no checker for its kind. Never raises.

    The kind comes from *path*'s name. *source* (a URL) is read instead of *path* when given.
    """
    kind = kind_for(path)
    if kind is None:
        return None
    check = {"image": "pillow", "pdf": "qpdf", "media": "ffprobe", "archive": "7z"}[kind]
    try:
        if kind == "media":
            # A local file (ingest) is decoded in full; a URL (the Wasabi backfill) only at the end.
            result = check_media(source) if source else check_media(path, full=True)
        else:
            result = CHECKERS[kind](source or path)
    except subprocess.TimeoutExpired as exc:
        return IntegrityResult("unchecked", check, f"timed out after {int(exc.timeout)} s")
    except Exception as exc:  # noqa: BLE001 - a broken tool must not stop ingest
        return IntegrityResult("unchecked", check, _short(f"{type(exc).__name__}: {exc}"))
    return result


def summarize(results: dict[str, IntegrityResult]) -> dict:
    counts = Counter(r.status for r in results.values())
    return {"checked": len(results), "ok": counts["ok"], "damaged": counts["damaged"], "unchecked": counts["unchecked"]}


def validation_event(results: dict[str, IntegrityResult], *, when: str, program: str, package_id: str,
                     package_uuid: str = "", repository_code: str = "hitsave"):
    """PREMIS validation event for a set of checked files (keys: bag paths, e.g. data/...)."""
    from premis_events import PremisEvent

    counts = summarize(results)
    damaged = [(path, r) for path, r in sorted(results.items()) if r.status == "damaged"]
    tools = ", ".join(sorted({r.check for r in results.values() if r.check}))
    note = f"{counts['ok']} ok, {counts['damaged']} damaged, {counts['unchecked']} unchecked."
    if damaged:
        shown = "; ".join(f"{path} ({r.detail})" for path, r in damaged[:50])
        more = f" ... and {len(damaged) - 50} more" if len(damaged) > 50 else ""
        note += f" Damaged files, preserved as received: {shown}{more}."
    note += " Per-file results are in metadata/inventory.json."
    return PremisEvent(
        event_type="validation",
        when=when,
        detail=(
            f"Checked {counts['checked']} images, PDFs, video/audio files and archives for damage "
            f"({tools}). Damaged files are kept unchanged."
        ),
        outcome="fail" if damaged else "pass",
        outcome_note=note,
        program=program,
        package_id=package_id,
        package_uuid=package_uuid,
        repository_code=repository_code,
    )
