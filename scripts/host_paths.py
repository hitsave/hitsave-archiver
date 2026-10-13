"""Operator host paths (bind mounts) — config/preservation/host-paths.yaml."""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def host_paths_yaml_path() -> Path:
    for path in (
        Path("/config/preservation/host-paths.yaml"),
        ROOT / "config" / "preservation" / "host-paths.yaml",
    ):
        if path.is_file():
            return path
    raise FileNotFoundError(
        "host-paths.yaml not found; copy config/preservation/host-paths.yaml.example"
    )


def load_host_paths_config() -> dict:
    data = yaml.safe_load(host_paths_yaml_path().read_text(encoding="utf-8")) or {}
    paths = data.get("host_paths")
    if not isinstance(paths, dict):
        raise ValueError("host-paths.yaml must define host_paths:")
    return paths


def resolve_host_path(key: str, *, repo_root: Path | None = None) -> Path:
    from config_require import require_str

    paths = load_host_paths_config()
    raw = require_str(paths, key, where="host_paths")
    p = Path(raw).expanduser()
    if not p.is_absolute():
        base = repo_root or ROOT
        p = (base / p).resolve()
    return p
