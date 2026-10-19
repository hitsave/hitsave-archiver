"""Omeka dcterms:description text for press-material items (SIP inventory, not Moby synopsis)."""
from __future__ import annotations

from pathlib import Path

_IMAGE_EXT = frozenset({"png", "jpg", "jpeg", "gif", "webp", "bmp", "tif", "tiff", "svg"})
_VIDEO_EXT = frozenset({"mp4", "webm", "mov", "avi", "mkv", "m4v"})
_AUDIO_EXT = frozenset({"mp3", "wav", "ogg", "flac", "aac", "m4a"})
_DOC_EXT = frozenset({"pdf", "doc", "docx", "txt", "rtf", "odt", "xls", "xlsx", "ppt", "pptx"})

_CATEGORY_ORDER = ("images", "documents", "videos", "audio", "files")
_CATEGORY_LABELS = {
    "images": "images",
    "documents": "documents",
    "videos": "videos",
    "audio": "audio files",
    "files": "files",
}


def _file_category(*, mime: str | None, path: str) -> str:
    mime_l = (mime or "").strip().lower()
    ext = Path(path).suffix.lower().lstrip(".")
    if mime_l.startswith("image/") or ext in _IMAGE_EXT:
        return "images"
    if mime_l.startswith("video/") or ext in _VIDEO_EXT:
        return "videos"
    if mime_l.startswith("audio/") or ext in _AUDIO_EXT:
        return "audio"
    if mime_l in ("application/pdf", "text/plain") or ext in _DOC_EXT:
        return "documents"
    if mime_l.startswith("text/"):
        return "documents"
    return "files"


def _format_including_phrase(labels: list[str]) -> str:
    if not labels:
        return "digital files from the press kit"
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]}"
    return ", ".join(labels[:-1]) + f", and {labels[-1]}"


def press_material_description(game_title: str, inventory: dict | None) -> str:
    """Describe the press kit contents, not the game synopsis."""
    title = (game_title or "").strip() or "this game"
    files = (inventory or {}).get("files") if isinstance(inventory, dict) else None
    if not isinstance(files, list) or not files:
        return f"Press material for {title}, including digital files from the press kit."

    categories: set[str] = set()
    for row in files:
        if not isinstance(row, dict):
            continue
        path = str(row.get("path") or row.get("relative_path") or "")
        if path.startswith("data/"):
            path = path[5:]
        categories.add(_file_category(mime=row.get("mime"), path=path))

    if categories == {"images"}:
        return f"Press material for {title}."

    label_categories = [
        c for c in _CATEGORY_ORDER if c in categories and c != "images"
    ]
    if not label_categories and "images" in categories:
        return f"Press material for {title}."
    labels = [_CATEGORY_LABELS[c] for c in label_categories]
    including = _format_including_phrase(labels)
    return f"Press material for {title}, including {including}."
