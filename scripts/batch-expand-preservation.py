#!/usr/bin/env python3
"""Expand a batch YAML into per-game preservation configs (no source moves).

Run inside ingest-worker (same /data/press-material mount as ingest):
  docker compose run --rm --entrypoint python ingest-worker \\
    /app/scripts/batch-expand-preservation.py /config/preservation/batch.yml
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("PyYAML required", file=sys.stderr)
    sys.exit(1)

APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT / "scripts"))
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
    paths = load_ingest_config()["paths"]
    press_root = Path(paths.get("press_material_root", "/data/press-material"))
    if batch.get("source_subpath") is not None:
        sub = str(batch["source_subpath"]).strip().strip("/")
        if not sub:
            raise SystemExit("source_subpath must not be empty")
        return press_root / sub
    if batch.get("source_parent"):
        legacy = Path(batch["source_parent"])
        try:
            legacy.relative_to(press_root)
        except ValueError as exc:
            raise SystemExit(
                f"source_parent must be under press_material_root ({press_root}); use source_subpath instead."
            ) from exc
        return legacy
    raise SystemExit(
        "batch.yml needs source_subpath (relative to paths.press_material_root in ingest.yaml)"
    )


def slug_key(batch_key: str, folder_name: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9]+", "-", folder_name.strip()).strip("-").lower()
    if not base:
        base = "game"
    return f"{batch_key}-{base}"[:120]


def main() -> None:
    cfg_root = config_root()
    default_batch = cfg_root / "preservation/batch.yml"
    batch_path = Path(sys.argv[1]) if len(sys.argv) > 1 else default_batch
    batch = yaml.safe_load(batch_path.read_text())
    batch_key = batch["batch_key"]
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
    press_root = Path(ingest["paths"]["press_material_root"])
    batch_output_dirs(batch_key, ingest, batch.get("output") or {})  # validate / override dirs exist in batch.yml

    gen_dir = cfg_root / "preservation" / "generated" / batch_key
    gen_dir.mkdir(parents=True, exist_ok=True)
    manifest = []

    for size, child in chosen:
        game_key = slug_key(batch_key, child.name)
        container_source = source_parent / child.name
        try:
            subpath = container_source.relative_to(press_root).as_posix()
        except ValueError as exc:
            raise SystemExit(f"Source folder not under press_material_root: {container_source}") from exc
        raw_title = f"{child.name}{batch.get('omeka_item_title_suffix', '')}"
        title = normalize_display_title(raw_title)
        game_cfg = {
            "source_subpath": subpath,
            "game_key": game_key,
            "omeka_item_title": title,
            "batch_key": batch_key,
        }
        out_path = gen_dir / f"{game_key}.yaml"
        out_path.write_text(yaml.safe_dump(game_cfg, sort_keys=False), encoding="utf-8")
        manifest.append(
            {
                "game_key": game_key,
                "folder_name": child.name,
                "source_bytes": size,
                "config": f"config/preservation/generated/{batch_key}/{game_key}.yaml",
            }
        )

    manifest_path = gen_dir / "manifest.yaml"
    manifest_path.write_text(
        yaml.safe_dump({"batch_key": batch_key, "games": manifest}, sort_keys=False),
        encoding="utf-8",
    )
    print(f"Wrote {len(manifest)} game configs under {gen_dir}")
    for row in manifest:
        print(f"  {row['game_key']}\t{row['source_bytes'] // 1024} KiB\t{row['folder_name']}")


if __name__ == "__main__":
    main()
