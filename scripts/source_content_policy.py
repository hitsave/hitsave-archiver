"""Press-material content policy checks (booru-style dumps, etc.)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


class ContentPolicyError(RuntimeError):
    """Source folder violates content policy (quarantine when enforce is on)."""

    def __init__(self, code: str, message: str, *, matches: list[str]) -> None:
        super().__init__(message)
        self.code = code
        self.matches = matches


@dataclass(frozen=True)
class BooruFilenameRules:
    enabled: bool = True
    min_double_underscore_segments: int = 3


@dataclass(frozen=True)
class ContentPolicyConfig:
    enforce: bool = True
    booru: BooruFilenameRules = BooruFilenameRules()


# Danbooru/safebooru wallpaper exports: {post_id}__tag__tag__...__wallpaper.jpg
_BOORU_WALLPAPER = re.compile(
    r"^\d+__(?:.+__)+wallpaper\.(?:jpe?g|png|webp|gif)$",
    re.IGNORECASE,
)


def load_content_policy(ingest_cfg: dict) -> ContentPolicyConfig:
    block = (ingest_cfg.get("content_policy") or {}).get("blockers") or {}
    booru = block.get("booru_tagged_filenames") or {}
    return ContentPolicyConfig(
        enforce=bool((ingest_cfg.get("content_policy") or {}).get("enforce", True)),
        booru=BooruFilenameRules(
            enabled=bool(booru.get("enabled", True)),
            min_double_underscore_segments=int(booru.get("min_double_underscore_segments", 3)),
        ),
    )


def is_booru_tagged_filename(name: str, *, rules: BooruFilenameRules) -> bool:
    if not rules.enabled:
        return False
    base = Path(name).name
    if not base or base.startswith("."):
        return False
    lower = base.lower()
    if _BOORU_WALLPAPER.match(lower):
        return True
    if lower.count("__") < rules.min_double_underscore_segments:
        return False
    # Post id + tag segments separated by double underscores (not single underscores).
    return bool(re.match(r"^\d+__(?:[^/\\]+__){2,}[^/\\]+\.[a-z0-9]{2,5}$", lower))


def scan_folder_for_booru(root: Path, rules: BooruFilenameRules) -> list[str]:
    hits: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        rel = path.relative_to(root).as_posix()
        if is_booru_tagged_filename(path.name, rules=rules):
            hits.append(rel)
    return hits


def check_source_content_policy(source: Path, ingest_cfg: dict) -> None:
    """Raise ContentPolicyError when enforce is on and blockers match."""
    policy = load_content_policy(ingest_cfg)
    if not policy.booru.enabled:
        return

    matches = scan_folder_for_booru(source, policy.booru)
    if not matches:
        return

    sample = ", ".join(matches[:5])
    extra = f" (+{len(matches) - 5} more)" if len(matches) > 5 else ""
    message = (
        f"Content policy blocked booru-style filenames ({len(matches)} file(s)): "
        f"{sample}{extra}"
    )
    if not policy.enforce:
        print(f"content_policy (warn only): {message}", flush=True)
        return

    raise ContentPolicyError("booru_tagged_filenames", message, matches=matches)


def write_quarantine_details(quarantine_dir: Path, *, exc: BaseException, matches: list[str] | None = None) -> None:
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    from datetime import datetime, timezone

    lines = [datetime.now(timezone.utc).isoformat(), str(exc), ""]
    if matches:
        lines.append("matched_files:")
        lines.extend(matches)
        lines.append("")
    (quarantine_dir / "error.txt").write_text("\n".join(lines), encoding="utf-8")
