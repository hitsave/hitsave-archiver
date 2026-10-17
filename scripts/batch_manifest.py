#!/usr/bin/env python3
"""Batch run identity: derive batch_key from operator batch file names, manifest paths."""
from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

_CONFIG_SLUG_RE = re.compile(r"[^a-zA-Z0-9]+")


def folder_config_slug(folder_name: str) -> str:
    """Filesystem-safe slug for generated per-game YAML (not ledger game_key)."""
    base = _CONFIG_SLUG_RE.sub("-", (folder_name or "").strip()).strip("-").lower()
    return (base[:120] or "game").strip("-")


def derive_batch_key(batch_path: Path, batch: dict | None = None) -> str:
    """Batch id from `batch-<slug>.yml` filename, with optional deprecated YAML override."""
    batch_path = batch_path.resolve()
    data = batch if batch is not None else {}
    if yaml is not None and not data and batch_path.is_file():
        loaded = yaml.safe_load(batch_path.read_text()) or {}
        if isinstance(loaded, dict):
            data = loaded

    explicit = data.get("batch_key")
    if explicit is not None and str(explicit).strip():
        return str(explicit).strip()

    stem = batch_path.stem
    if stem.startswith("batch-"):
        key_part = stem[len("batch-") :]
    elif stem == "batch":
        raise SystemExit(
            "Name the batch file batch-<slug>.yml (e.g. batch-repcopies-f-5.yml) "
            "so batch_key is derived automatically."
        )
    else:
        key_part = stem

    key = _CONFIG_SLUG_RE.sub("-", key_part.strip()).strip("-").lower()
    if not key:
        raise SystemExit(f"Could not derive batch_key from batch file name: {batch_path.name}")
    return key[:120]


def generated_manifest_path(cfg_root: Path, batch_path: Path, batch: dict | None = None) -> Path:
    key = derive_batch_key(batch_path, batch)
    return cfg_root / "preservation" / "generated" / key / "manifest.yaml"


def manifest_game_label(game: dict) -> str:
    return str(
        game.get("folder_name")
        or game.get("config_slug")
        or game.get("game_key")
        or game.get("config")
        or "game"
    )


def resolve_manifest_path(raw: Path, cfg_root: Path) -> Path:
    path = raw.resolve()
    if path.name == "manifest.yaml":
        return path
    if path.suffix in (".yaml", ".yml"):
        batch = yaml.safe_load(path.read_text()) or {} if yaml else {}
        return generated_manifest_path(cfg_root, path, batch if isinstance(batch, dict) else {})
    if path.is_dir():
        manifest = path / "manifest.yaml"
        if manifest.is_file():
            return manifest
    raise SystemExit(f"Cannot resolve batch manifest from {raw}")


def resolve_game_config_path(manifest: dict, game: dict, *, cfg_root: Path, repo_root: Path) -> Path:
    batch_key = str(manifest["batch_key"])
    cfg = Path(game["config"])
    if cfg.is_file():
        return cfg
    if not cfg.is_absolute():
        candidate = cfg_root / "preservation" / "generated" / batch_key / cfg.name
        if candidate.is_file():
            return candidate
        candidate = repo_root / game["config"]
        if candidate.is_file():
            return candidate
    raise SystemExit(f"Game config not found: {game.get('config')}")


def main() -> None:
    if len(sys.argv) < 3 or sys.argv[1] != "derive-key":
        raise SystemExit("Usage: batch_manifest.py derive-key <path/to/batch.yml>")
    print(derive_batch_key(Path(sys.argv[2])))


if __name__ == "__main__":
    main()
