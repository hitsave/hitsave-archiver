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

# Larger images are not decoded: 500 MP of RGBA is 2 GB of RAM.
MAX_IMAGE_PIXELS = 500_000_000
TIMEOUT_SECONDS = 900
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
        return IntegrityResult("damaged", "pillow", "not a readable image (unknown or corrupt header)")
    except (OSError, SyntaxError, ValueError, EOFError) as exc:
        return IntegrityResult("damaged", "pillow", _short(str(exc)))
    finally:
        (ImageFile.LOAD_TRUNCATED_IMAGES, Image.MAX_IMAGE_PIXELS,
         PngImagePlugin.MAX_TEXT_CHUNK, PngImagePlugin.MAX_TEXT_MEMORY) = saved
    return IntegrityResult("ok", "pillow")


def _display_name(target: Path | str) -> str:
    text = str(target)
    if "://" in text:
        from urllib.parse import unquote, urlparse
        return Path(unquote(urlparse(text).path)).name
    return Path(text).name


def _run(cmd: list[str], target: Path | str) -> subprocess.CompletedProcess:
    """Run a checker. Tools quote the path they read (a temp file, or a signed URL in the
    backfill); that is replaced by the bare file name before anything is recorded."""
    proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=TIMEOUT_SECONDS, check=False)
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


def check_media(path: Path | str) -> IntegrityResult:
    """*path* may be an https URL (a presigned Wasabi link): ffmpeg reads only what it needs."""
    probe = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], path)
    duration = (probe.stdout or "").strip()
    if probe.returncode != 0 or not duration or duration == "N/A":
        return IntegrityResult("damaged", "ffprobe", _short(probe.stderr) if probe.stderr.strip() else "no duration")
    # Decode the last 15 seconds: a cut-off file fails here even when its header is intact.
    tail = _run(["ffmpeg", "-v", "error", "-xerror", "-sseof", "-15", "-i", str(path), "-f", "null", "-"], path)
    if tail.returncode != 0:
        return IntegrityResult("damaged", "ffmpeg", _short(tail.stderr))
    return IntegrityResult("ok", "ffprobe")


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
    needed = ["qpdf", "ffprobe", "ffmpeg"]
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
        result = CHECKERS[kind](source or path)
    except subprocess.TimeoutExpired:
        return IntegrityResult("unchecked", check, f"timed out after {TIMEOUT_SECONDS} s")
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
