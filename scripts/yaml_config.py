"""Load YAML config files (UTF-8)."""
from __future__ import annotations

from pathlib import Path

import yaml


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
