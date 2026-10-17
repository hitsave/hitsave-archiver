"""Load YAML config files (UTF-8)."""
from __future__ import annotations

import json
from pathlib import Path

import yaml


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def dumps_json(obj, *, indent: int | None = None) -> str:
    """JSON for preservation artifacts (UTF-8 literals, not \\u escapes)."""
    text = json.dumps(obj, ensure_ascii=False, indent=indent)
    if indent is not None:
        return text + "\n"
    return text
