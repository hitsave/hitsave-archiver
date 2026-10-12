"""Shared helpers for Hit Save BagIt preservation ingest."""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_ingest_config() -> dict:
    for path in (
        Path("/config/preservation/ingest.yaml"),
        ROOT / "config" / "preservation" / "ingest.yaml",
    ):
        if path.is_file():
            data = yaml.safe_load(path.read_text()) or {}
            return data
    raise FileNotFoundError("ingest.yaml not found under /config/preservation/ or repo config/")


def batch_output_dirs(batch_key: str, ingest: dict, out_cfg: dict | None = None) -> tuple[Path, Path, Path]:
    """Default batch bag/staging dirs under paths.output_root (container paths)."""
    out_cfg = out_cfg or {}
    paths = ingest["paths"]
    root = Path(paths["output_root"])
    bag_sub = paths.get("bag_subdir", "bag")
    aip_sub = paths.get("aip_subdir", "aip")
    rel = Path("batch") / batch_key
    bag_dir = Path(out_cfg.get("bag_dir", root / bag_sub / rel))
    aip_dir = Path(out_cfg.get("aip_dir", root / aip_sub / rel))
    staging_root = Path(out_cfg.get("staging_dir", root / ".staging" / rel))
    return bag_dir, aip_dir, staging_root


def resolve_game_config(cfg: dict, *, agent_version: str | None = None) -> dict:
    """Apply ingest.yaml paths, defaults, and preservation metadata to a game config."""
    ingest = load_ingest_config()
    paths = ingest["paths"]
    out = dict(cfg)
    press_root = Path(paths["press_material_root"])
    output_root = Path(paths["output_root"])
    bag_sub = paths.get("bag_subdir", "bag")
    aip_sub = paths.get("aip_subdir", "aip")

    if out.get("source_subpath") and not out.get("source_game_folder"):
        sub = str(out["source_subpath"]).strip().strip("/")
        if not sub:
            raise ValueError("source_subpath must not be empty")
        out["source_game_folder"] = str(press_root / sub)

    game_key = out.get("game_key")
    if not game_key and out.get("source_game_folder"):
        game_key = Path(out["source_game_folder"]).name
        out["game_key"] = game_key

    batch_key = out.get("batch_key")
    batch_segment = Path("batch") / batch_key if batch_key else None

    if game_key and not out.get("staging_dir"):
        stag_base = output_root / ".staging"
        if batch_segment:
            stag_base = stag_base / batch_segment
        out["staging_dir"] = str(stag_base / game_key)

    if game_key and not out.get("bagit_bag_dir"):
        bag_base = output_root / bag_sub
        if batch_segment:
            bag_base = bag_base / batch_segment
        out["bagit_bag_dir"] = str(bag_base / game_key)

    if game_key and not out.get("aip_bag_dir"):
        aip_base = output_root / aip_sub
        if batch_segment:
            aip_base = aip_base / batch_segment
        out["aip_bag_dir"] = str(aip_base / game_key)

    defaults = ingest.get("defaults") or {}
    out.setdefault("max_files", defaults.get("max_files", 5000))
    out.setdefault("max_total_bytes", defaults.get("max_total_bytes", 5368709120))

    pres = dict(ingest.get("preservation") or {})
    pres.update(out.get("preservation") or {})
    if agent_version:
        pres["agent_version"] = agent_version
    out["preservation"] = pres

    from ingest_kind import resolve_ingest_kind  # noqa: WPS433 — avoid circular import at module load

    out["ingest_kind"] = resolve_ingest_kind(out)
    return out


def _packaging_yaml_path() -> Path:
    for path in (
        Path("/config/preservation/packaging.yaml"),
        ROOT / "config" / "preservation" / "packaging.yaml",
    ):
        if path.is_file():
            return path
    raise FileNotFoundError("packaging.yaml not found under /config/preservation/ or repo config/")


def load_packaging_yaml_root() -> dict:
    return yaml.safe_load(_packaging_yaml_path().read_text()) or {}


def load_packaging_config() -> dict:
    return load_packaging_yaml_root().get("preservation") or {}


def wasabi_unpacked_prefix(storage_prefix: str) -> str:
    """S3 key prefix for an unpacked bag (trailing slash)."""
    return f"{storage_prefix.strip('/')}/"


def bagit_package_s3_uri_for_prefix(bucket: str, package_storage_prefix: str) -> str:
    return f"s3://{bucket.strip()}/{package_storage_prefix.strip('/')}/"


def wasabi_bagit_unpacked_prefix(
    wasabi_prefix: str,
    *,
    package_id: str,
    sort_letter: str,
) -> str:
    """S3 key prefix for an unpacked BagIt package (no bucket, no trailing slash)."""
    storage = load_packaging_yaml_root().get("storage") or {}
    wasabi = storage.get("wasabi") or {}
    pattern = str(
        wasabi.get("unpacked_key_pattern") or "{prefix}/{sort_letter}/{package_id}"
    )
    prefix = wasabi_prefix.strip("/")
    pid = package_id.strip("/")
    letter = sort_letter.strip("/") or "X"
    return pattern.format(
        prefix=prefix,
        sort_letter=letter,
        package_id=pid,
        game_key=pid,
    ).strip("/")


def should_skip(path: Path) -> bool:
    name = path.name
    return name.startswith(".") or name == "desktop.ini"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_id(prefix: str = "uuid") -> str:
    return f"{prefix}-{uuid.uuid4()}"
