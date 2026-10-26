"""Shared helpers for Hit Save BagIt preservation ingest."""
from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

from config_require import require_dict, require_key, require_str

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
    paths = require_dict(ingest, "paths", where="ingest.yaml")
    root = Path(require_str(paths, "output_root", where="ingest.yaml paths"))
    bag_sub = require_str(paths, "bag_subdir", where="ingest.yaml paths")
    aip_sub = require_str(paths, "aip_subdir", where="ingest.yaml paths")
    rel = Path("batch") / batch_key
    bag_dir = Path(out_cfg.get("bag_dir", root / bag_sub / rel))
    aip_dir = Path(out_cfg.get("aip_dir", root / aip_sub / rel))
    staging_root = Path(out_cfg.get("staging_dir", root / ".staging" / rel))
    return bag_dir, aip_dir, staging_root


def resolve_game_config(cfg: dict, *, agent_version: str | None = None) -> dict:
    """Apply ingest.yaml paths, defaults, and preservation metadata to a game config."""
    ingest = load_ingest_config()
    paths = require_dict(ingest, "paths", where="ingest.yaml")
    out = dict(cfg)
    submission_meta = out.get("submission") or {}
    if not out.get("batch_key"):
        sub_batch = str(submission_meta.get("batch_key") or "").strip()
        if sub_batch:
            out["batch_key"] = sub_batch
    if not out.get("batch_key"):
        env_batch = os.environ.get("HITSAVE_BATCH_KEY", "").strip()
        if env_batch:
            out["batch_key"] = env_batch
    press_root = Path(require_str(paths, "press_material_root", where="ingest.yaml paths"))
    output_root = Path(require_str(paths, "output_root", where="ingest.yaml paths"))
    bag_sub = require_str(paths, "bag_subdir", where="ingest.yaml paths")
    aip_sub = require_str(paths, "aip_subdir", where="ingest.yaml paths")

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
    portable_submission = bool(submission_meta.get("batch_key"))
    if portable_submission and batch_key:
        batch_segment = Path("submissions") / batch_key
    else:
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

    defaults = require_dict(ingest, "defaults", where="ingest.yaml")
    if "max_files" not in out:
        out["max_files"] = int(require_key(defaults, "max_files", where="ingest.yaml defaults"))
    if "max_total_bytes" not in out:
        raw = defaults.get("max_total_bytes")
        if raw is None:
            out["max_total_bytes"] = 0
        else:
            out["max_total_bytes"] = int(require_key(defaults, "max_total_bytes", where="ingest.yaml defaults"))

    pres = dict(require_dict(ingest, "preservation", where="ingest.yaml"))
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


def wasabi_bucket_config_key(ingest_kind: str) -> str:
    """Key under wasabi.buckets for an ingest kind id (see ingest-kinds.yaml)."""
    kind = str(ingest_kind or "").strip().casefold().replace("-", "_")
    if not kind:
        raise ValueError("ingest_kind is required to resolve Wasabi bucket")
    if kind == "dump":
        return "dumps"
    return kind


def wasabi_s3_region(wasabi: dict) -> str:
    return require_str(wasabi, "region", where="ingest.yaml wasabi")


def resolve_wasabi_bucket(wasabi: dict, ingest_kind: str) -> str:
    """Bucket for an ingest kind from wasabi.buckets only (no fallback)."""
    buckets = wasabi.get("buckets")
    if not isinstance(buckets, dict) or not buckets:
        raise RuntimeError(
            "wasabi.buckets must list a bucket per ingest kind; "
            "wasabi.bucket fallback is not used"
        )
    cfg_key = wasabi_bucket_config_key(ingest_kind)
    named = buckets.get(cfg_key)
    if not named or not str(named).strip():
        raise RuntimeError(
            f"Missing or empty wasabi.buckets.{cfg_key!r} "
            f"(ingest_kind={ingest_kind!r})"
        )
    return str(named).strip()


def wasabi_bucket_for_storage_prefix(
    wasabi: dict,
    package_storage_prefix: str,
    *,
    ingest_kind: str | None = None,
) -> str:
    """Resolve bucket from ledger kind or the first path segment (press-material vs dumps)."""
    if ingest_kind:
        return resolve_wasabi_bucket(wasabi, ingest_kind)
    root = str(package_storage_prefix or "").strip("/").split("/")[0].casefold()
    if not root:
        raise RuntimeError(
            f"Cannot infer Wasabi bucket from empty storage prefix; set ingest_kind on the ledger row"
        )
    from ingest_kind import wasabi_root_to_ingest_kind_map

    kind = wasabi_root_to_ingest_kind_map().get(root)
    if not kind:
        known = ", ".join(sorted(wasabi_root_to_ingest_kind_map()))
        raise RuntimeError(
            f"Cannot infer Wasabi bucket from storage prefix {package_storage_prefix!r} "
            f"(unknown root {root!r}; known wasabi_root values: {known})"
        )
    return resolve_wasabi_bucket(wasabi, kind)


def wasabi_unpacked_prefix(storage_prefix: str) -> str:
    """S3 key prefix for an unpacked bag (trailing slash)."""
    return f"{storage_prefix.strip('/')}/"


def bagit_package_s3_uri_for_prefix(bucket: str, package_storage_prefix: str) -> str:
    return f"s3://{bucket.strip()}/{package_storage_prefix.strip('/')}/"


def material_root_for_kind(ingest_kind: str, paths: dict) -> Path:
    """Host/container root used to compute relative_sip_path for Wasabi keys."""
    kind = str(ingest_kind or "").strip().casefold().replace("-", "_")
    if kind == "analog":
        analog = require_str(paths, "analog_material_root", where="ingest.yaml paths")
        return Path(analog)
    return Path(require_str(paths, "press_material_root", where="ingest.yaml paths"))


def _validate_relative_sip_path(rel: str) -> str:
    cleaned = str(rel or "").strip().strip("/")
    if not cleaned:
        raise ValueError("relative_sip_path is required for this Wasabi key layout")
    for part in cleaned.split("/"):
        if part in ("", ".", ".."):
            raise ValueError(f"invalid relative_sip_path: {rel!r}")
    return cleaned


def wasabi_key_pattern_for_kind(ingest_kind: str) -> str:
    from ingest_kind import ingest_kind_spec

    custom = ingest_kind_spec(ingest_kind).get("wasabi_key_pattern")
    if custom and str(custom).strip():
        return str(custom).strip()
    storage = require_dict(load_packaging_yaml_root(), "storage", where="packaging.yaml")
    wasabi = require_dict(storage, "wasabi", where="packaging.yaml storage")
    return require_str(wasabi, "unpacked_key_pattern", where="packaging.yaml storage.wasabi")


def wasabi_package_storage_prefix(
    ingest_kind: str,
    *,
    package_id: str,
    sort_letter: str,
    relative_sip_path: str = "",
) -> str:
    """S3 key prefix for an unpacked bag (no bucket, no trailing slash)."""
    from ingest_kind import wasabi_root_for_kind

    pattern = wasabi_key_pattern_for_kind(ingest_kind)
    prefix_root = wasabi_root_for_kind(ingest_kind)
    rel = (
        _validate_relative_sip_path(relative_sip_path)
        if "{relative_sip_path}" in pattern
        else str(relative_sip_path or "").strip().strip("/")
    )
    if "{relative_sip_path}" in pattern and not rel:
        raise ValueError(f"relative_sip_path required for ingest kind {ingest_kind!r}")
    pid = package_id.strip("/")
    letter = sort_letter.strip("/") or "X"
    return pattern.format(
        prefix=prefix_root.strip("/"),
        sort_letter=letter,
        package_id=pid,
        game_key=pid,
        relative_sip_path=rel,
    ).strip("/")


def bagit_payload_relpath(source: Path, file_path: Path, *, preserve_source_folder: bool) -> str:
    """Relative path under BagIt payload dir (before data/ prefix in manifests)."""
    inner = file_path.relative_to(source).as_posix()
    if not preserve_source_folder:
        return inner
    folder = source.name.strip()
    if not folder or folder in (".", ".."):
        raise ValueError(f"Cannot preserve source folder name for SIP path: {source}")
    return f"{folder}/{inner}" if inner else folder


def should_skip(path: Path) -> bool:
    name = path.name
    if name.startswith(".") or name == "desktop.ini":
        return True
    return "_ocr_crop_debug" in path.parts


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
