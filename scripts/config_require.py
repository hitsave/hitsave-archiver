"""Require keys from YAML config — no silent fallbacks."""
from __future__ import annotations

from typing import Any


def require_key(cfg: dict[str, Any], key: str, *, where: str) -> Any:
    if key not in cfg:
        raise ValueError(f"Missing required key {where}.{key}")
    val = cfg[key]
    if val is None:
        raise ValueError(f"Missing required key {where}.{key} (null)")
    return val


def require_str(cfg: dict[str, Any], key: str, *, where: str) -> str:
    val = require_key(cfg, key, where=where)
    text = str(val).strip()
    if not text:
        raise ValueError(f"Empty required string {where}.{key}")
    return text


def require_dict(cfg: dict[str, Any], key: str, *, where: str) -> dict[str, Any]:
    val = require_key(cfg, key, where=where)
    if not isinstance(val, dict):
        raise ValueError(f"Required dict {where}.{key}")
    return val


def require_bool(cfg: dict[str, Any], key: str, *, where: str) -> bool:
    if key not in cfg:
        raise ValueError(f"Missing required key {where}.{key}")
    val = cfg[key]
    if not isinstance(val, bool):
        raise ValueError(f"Required boolean {where}.{key}")
    return val
