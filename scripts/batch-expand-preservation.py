#!/usr/bin/env python3
"""Expand a batch YAML into per-game preservation configs (no source moves).

Run inside ingest-worker (same /data/press-material mount as ingest):
  docker compose run --rm --entrypoint python ingest-worker \\
    /app/scripts/batch-expand-preservation.py /config/preservation/batch-repcopies-f-5.yml

batch_key is derived from the batch file name (batch-<slug>.yml). Per-game YAML lists only
ingest_kind, source_subpath, and omeka_item_title; ledger game_key is assigned at ingest.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("PyYAML required", file=sys.stderr)
    sys.exit(1)

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT / "scripts"))
from batch_manifest import derive_batch_key, folder_config_slug  # noqa: E402
from normalize_display_title import normalize_display_title  # noqa: E402
from preservation_common import batch_output_dirs, load_ingest_config  # noqa: E402


def config_root() -> Path:
    env = os.environ.get("HITSAVE_CONFIG_ROOT")
    if env:
        return Path(env)
    if Path("/config/preservation/ingest.yaml").is_file():
        return Path("/config")
    return APP_ROOT / "config"


def folder_bytes(path: Path) -> int:
    total = 0
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if name.startswith("."):
                continue
            fp = Path(dirpath) / name
            try:
                total += fp.stat().st_size
            except OSError:
                pass
    return total


def resolve_source_parent(batch: dict, cfg_root: Path) -> Path:
    """Folder to scan: press_material_root + source_subpath (see ingest.yaml)."""
    from config_require import require_dict, require_str

    paths = require_dict(load_ingest_config(), "paths", where="ingest.yaml")
    press_root = Path(require_str(paths, "press_material_root", where="ingest.yaml paths"))
    if batch.get("source_subpath") is not None:
        sub = str(batch["source_subpath"]).strip().strip("/")
        if not sub:
            raise SystemExit("source_subpath must not be empty")
        return press_root / sub
    raise SystemExit(
        "batch.yml needs source_subpath (relative to paths.press_material_root in ingest.yaml)"
    )


def main() -> None:
    cfg_root = config_root()
    default_batch = cfg_root / "preservation/batch.yml"
    batch_path = Path(sys.argv[1]) if len(sys.argv) > 1 else default_batch
    batch = yaml.safe_load(batch_path.read_text()) or {}
    batch_key = derive_batch_key(batch_path, batch)
    source_parent = resolve_source_parent(batch, cfg_root)
    if not source_parent.is_dir():
        raise SystemExit(
            f"Source parent not found: {source_parent}\n"
            "Run via ingest-worker so press-material is mounted (see scripts/batch-expand-preservation.sh)."
        )

    sel = batch.get("select") or {}
    min_b = int(sel.get("min_bytes", 0))
    max_b = int(sel.get("max_bytes", 2**62))
    limit = int(sel.get("limit", 25))
    sort_by = sel.get("sort_by", "size")

    rows: list[tuple[int, Path]] = []
    for child in sorted(source_parent.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        size = folder_bytes(child)
        if min_b <= size <= max_b:
            rows.append((size, child))

    if sort_by == "size":
        rows.sort(key=lambda r: r[0])
    elif sort_by == "name":
        rows.sort(key=lambda r: r[1].name.lower())

    chosen = rows[:limit]
    if len(chosen) < limit:
        print(
            f"Warning: only {len(chosen)} folders matched (wanted {limit})",
            file=sys.stderr,
        )

    ingest = load_ingest_config()
    batch_ingest_kind = batch.get("ingest_kind")
    if not batch_ingest_kind or not str(batch_ingest_kind).strip():
        raise SystemExit(
            "batch.yml must set ingest_kind (e.g. press_material); see config/preservation/ingest-kinds.yaml"
        )
    press_root = Path(ingest["paths"]["press_material_root"])
    batch_output_dirs(batch_key, ingest, batch.get("output") or {})

    gen_dir = cfg_root / "preservation" / "generated" / batch_key
    gen_dir.mkdir(parents=True, exist_ok=True)
    manifest = []

    for size, child in chosen:
        config_slug = folder_config_slug(child.name)
        container_source = source_parent / child.name
        try:
            subpath = container_source.relative_to(press_root).as_posix()
        except ValueError as exc:
            raise SystemExit(f"Source folder not under press_material_root: {container_source}") from exc
        raw_title = f"{child.name}{batch.get('omeka_item_title_suffix', '')}"
        title = normalize_display_title(raw_title)
        game_cfg = {
            "source_subpath": subpath,
            "ingest_kind": str(batch_ingest_kind).strip(),
            "omeka_item_title": title,
        }
        out_path = gen_dir / f"{config_slug}.yaml"
        out_path.write_text(yaml.safe_dump(game_cfg, sort_keys=False), encoding="utf-8")
        manifest.append(
            {
                "config_slug": config_slug,
                "folder_name": child.name,
                "source_subpath": subpath,
                "source_bytes": size,
                "config": f"config/preservation/generated/{batch_key}/{config_slug}.yaml",
            }
        )

    manifest_path = gen_dir / "manifest.yaml"
    manifest_path.write_text(
        yaml.safe_dump({"batch_key": batch_key, "batch_file": batch_path.name, "games": manifest}, sort_keys=False),
        encoding="utf-8",
    )
    print(f"batch_key={batch_key} (from {batch_path.name})")
    print(f"Wrote {len(manifest)} game configs under {gen_dir}")
    for row in manifest:
        print(f"  {row['config_slug']}\t{row['source_bytes'] // 1024} KiB\t{row['folder_name']}")


if __name__ == "__main__":
    main()
