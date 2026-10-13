"""Resolve ingest kind and catalog behavior for game/submission YAML."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]

_ALIASES = {
    "press-material": "press_material",
    "pressmaterial": "press_material",
    "dump": "dump",
    "game_dump": "dump",
    "game-dump": "dump",
    "gamedump": "dump",
    "disc_dump": "dump",
    "disc-dump": "dump",
    "redump": "dump",
}


def _kinds_config_path() -> Path:
    for path in (
        Path("/config/preservation/ingest-kinds.yaml"),
        ROOT / "config" / "preservation" / "ingest-kinds.yaml",
    ):
        if path.is_file():
            return path
    raise FileNotFoundError("ingest-kinds.yaml not found under /config/preservation/ or repo config/")


def load_ingest_kinds_config() -> dict[str, Any]:
    data = yaml.safe_load(_kinds_config_path().read_text()) or {}
    return data


def _normalize_kind_id(raw: str) -> str:
    key = str(raw).strip().casefold().replace("-", "_")
    return _ALIASES.get(key, key)


def resolve_ingest_kind(game_cfg: dict | None) -> str:
    """Kind id from game YAML — ingest_kind is required on every ingest config."""
    cfg = game_cfg or {}
    kinds = load_ingest_kinds_config().get("ingest_kinds") or {}
    raw = cfg.get("ingest_kind")
    if raw is None or str(raw).strip() == "":
        raise ValueError(
            "game config must set ingest_kind (see config/preservation/ingest-kinds.yaml); "
            "see config/preservation/ingest-kinds.yaml"
        )
    kind = _normalize_kind_id(str(raw))
    if kind not in kinds:
        raise ValueError(f"Unknown ingest kind {raw!r}; see config/preservation/ingest-kinds.yaml")
    return kind


def ingest_kind_spec(kind_id: str) -> dict[str, Any]:
    kinds = load_ingest_kinds_config().get("ingest_kinds") or {}
    spec = kinds.get(kind_id)
    if not spec:
        raise ValueError(f"Unknown ingest kind {kind_id!r}")
    return dict(spec)


def moby_enrich_enabled(game_cfg: dict | None) -> bool:
    cfg = game_cfg or {}
    if cfg.get("moby_enrich") is not None:
        return bool(cfg["moby_enrich"])
    return bool(ingest_kind_spec(resolve_ingest_kind(cfg)).get("moby_enrich", False))


def storage_profile_for_kind(kind_id: str) -> str:
    from config_require import require_str

    spec = ingest_kind_spec(kind_id)
    profile = require_str(spec, "storage_profile", where=f"ingest_kinds.{kind_id}").casefold()
    if profile != "bagit_unpacked":
        raise ValueError(
            f"ingest_kinds.{kind_id}.storage_profile must be bagit_unpacked (got {profile!r})"
        )
    return profile


def wasabi_root_for_kind(kind_id: str) -> str:
    from config_require import require_str

    spec = ingest_kind_spec(kind_id)
    return require_str(spec, "wasabi_root", where=f"ingest_kinds.{kind_id}").strip().strip("/")


def wasabi_root_to_ingest_kind_map() -> dict[str, str]:
    """Map Wasabi key first segment (wasabi_root) → ingest kind id."""
    kinds = load_ingest_kinds_config().get("ingest_kinds") or {}
    out: dict[str, str] = {}
    for kind_id, spec in kinds.items():
        if not isinstance(spec, dict):
            continue
        root = wasabi_root_for_kind(str(kind_id)).casefold()
        if root in out and out[root] != kind_id:
            raise ValueError(f"Duplicate wasabi_root {root!r} on ingest kinds")
        out[root] = str(kind_id)
    if not out:
        raise ValueError("ingest-kinds.yaml defines no ingest_kinds with wasabi_root")
    return out


def moby_omeka_profile_for_kind(kind_id: str) -> str:
    profile = str(ingest_kind_spec(kind_id).get("moby_omeka_profile") or "none").strip().casefold()
    profile = profile.replace("-", "_")
    if profile in ("press_material", "game_catalog"):
        return profile
    return "none"


def moby_omeka_profile_for_game_cfg(game_cfg: dict | None) -> str:
    return moby_omeka_profile_for_kind(resolve_ingest_kind(game_cfg))


def find_game_config_path(game_key: str, config_root: Path | None = None) -> Path | None:
    root = config_root or (Path("/config") if Path("/config/preservation").is_dir() else ROOT / "config")
    gen = root / "preservation" / "generated"
    if gen.is_dir():
        matches = list(gen.rglob(f"{game_key}.yaml"))
        if len(matches) == 1:
            return matches[0]
    p = root / "preservation" / f"{game_key}.yaml"
    if p.is_file():
        return p
    return None


def ingest_kind_for_game_key(game_key: str, config_root: Path | None = None) -> str:
    path = find_game_config_path(game_key, config_root)
    if path is None:
        raise ValueError(
            f"No game config found for {game_key!r} and ingest_kind not provided; "
            "set ingest_kind on the game YAML or ledger row"
        )
    raw = yaml.safe_load(path.read_text()) or {}
    return resolve_ingest_kind(raw)


def resolve_omeka_moby_profile(
    *,
    game_cfg: dict | None = None,
    ingest_kind: str | None = None,
    game_key: str | None = None,
    config_root: Path | None = None,
) -> str:
    kind_raw = str(ingest_kind).strip() if ingest_kind else ""
    if kind_raw:
        return moby_omeka_profile_for_kind(_normalize_kind_id(kind_raw))
    if game_cfg:
        return moby_omeka_profile_for_game_cfg(game_cfg)
    if game_key:
        path = find_game_config_path(game_key, config_root)
        if path is not None:
            raw = yaml.safe_load(path.read_text()) or {}
            if raw.get("ingest_kind"):
                return moby_omeka_profile_for_game_cfg(raw)
    raise ValueError(
        "Cannot resolve Omeka Moby profile: set ingest_kind on the ledger row or game YAML "
        f"(game_key={game_key!r})"
    )
