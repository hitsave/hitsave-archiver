"""Resolve submission provenance from config/preservation/submission-sources.yaml."""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_submission_sources_config() -> dict:
    for path in (
        Path("/config/preservation/submission-sources.yaml"),
        ROOT / "config" / "preservation" / "submission-sources.yaml",
    ):
        if path.is_file():
            data = yaml.safe_load(path.read_text()) or {}
            return data.get("sources") or {}
    return {}


def resolve_submission_source(source_game_folder: Path) -> dict | None:
    """Longest matching container_path_prefix wins."""
    sources = load_submission_sources_config()
    if not sources:
        return None
    folder = source_game_folder.resolve()
    best: tuple[int, dict] | None = None
    for _name, cfg in sources.items():
        if not isinstance(cfg, dict):
            continue
        prefix = str(cfg.get("container_path_prefix") or "").rstrip("/")
        if not prefix:
            continue
        try:
            Path(prefix).resolve()
        except OSError:
            continue
        prefix_path = Path(prefix)
        try:
            folder.relative_to(prefix_path)
            match_len = len(prefix)
        except ValueError:
            continue
        if best is None or match_len > best[0]:
            best = (match_len, cfg)
    return best[1] if best else None
