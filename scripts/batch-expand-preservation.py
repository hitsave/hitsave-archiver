#!/usr/bin/env python3
"""Expand a batch YAML into per-game preservation configs (no source moves)."""
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from normalize_display_title import normalize_display_title  # noqa: E402


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


def slug_key(batch_key: str, folder_name: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9]+", "-", folder_name.strip()).strip("-").lower()
    if not base:
        base = "game"
    return f"{batch_key}-{base}"[:120]


def main() -> None:
    batch_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "config/preservation/batch-repcopies-t.yaml"
    batch = yaml.safe_load(batch_path.read_text())
    batch_key = batch["batch_key"]
    container_parent = Path(batch["source_parent"])
    parent = Path(batch.get("host_source_parent") or batch["source_parent"])
    if not parent.is_dir():
        raise SystemExit(f"Source parent not found: {parent}")

    sel = batch.get("select") or {}
    min_b = int(sel.get("min_bytes", 0))
    max_b = int(sel.get("max_bytes", 2**62))
    limit = int(sel.get("limit", 25))
    sort_by = sel.get("sort_by", "size")

    rows: list[tuple[int, Path]] = []
    for child in sorted(parent.iterdir()):
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

    out_cfg = batch.get("output") or {}
    dip_dir = Path(out_cfg.get("dip_dir", f"/output/dip/batch/{batch_key}"))
    aip_dir = Path(out_cfg.get("aip_dir", f"/output/aip/batch/{batch_key}"))
    staging_root = Path(out_cfg.get("staging_dir", f"/output/.staging/batch/{batch_key}"))

    gen_dir = ROOT / "config" / "preservation" / "generated" / batch_key
    gen_dir.mkdir(parents=True, exist_ok=True)
    manifest = []

    for size, child in chosen:
        game_key = slug_key(batch_key, child.name)
        container_source = container_parent / child.name
        raw_title = f"{child.name}{batch.get('omeka_item_title_suffix', '')}"
        title = normalize_display_title(raw_title)
        game_cfg = {
            "source_game_folder": str(container_source),
            "output_tar": str(dip_dir / f"{game_key}.tar"),
            "staging_dir": str(staging_root / game_key),
            "aip_bag_dir": str(aip_dir / game_key),
            "game_key": game_key,
            "omeka_item_title": title,
            "max_files": batch.get("max_files", 5000),
            "max_total_bytes": batch.get("max_total_bytes", 5368709120),
            "preservation": batch.get("preservation") or {},
            "batch_key": batch_key,
        }
        out_path = gen_dir / f"{game_key}.yaml"
        out_path.write_text(yaml.safe_dump(game_cfg, sort_keys=False), encoding="utf-8")
        manifest.append(
            {
                "game_key": game_key,
                "folder_name": child.name,
                "source_bytes": size,
                "config": str(out_path.relative_to(ROOT)),
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
