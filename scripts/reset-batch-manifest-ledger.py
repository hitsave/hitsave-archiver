#!/usr/bin/env python3
"""Reset ledger rows for every game listed in a batch manifest (re-ingest + Omeka)."""
from __future__ import annotations

import sys
from pathlib import Path

try:
    import psycopg
    import yaml
except ImportError as e:
    print(e, file=sys.stderr)
    sys.exit(1)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from batch_ledger import config_root, pg_connect, source_path_from_game_config  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: reset-batch-manifest-ledger.py <batch.yml|manifest.yaml>")
    raw = Path(sys.argv[1])
    if not raw.is_absolute() and not raw.is_file():
        candidate = config_root() / raw
        if candidate.is_file():
            raw = candidate
    path = raw.resolve()
    if path.name == "manifest.yaml" or path.parent.name.startswith("repcopies-"):
        manifest_path = path if path.name == "manifest.yaml" else path.parent / "manifest.yaml"
        if not manifest_path.is_file():
            batch = yaml.safe_load(path.read_text())
            batch_key = batch["batch_key"]
            manifest_path = config_root() / "preservation/generated" / batch_key / "manifest.yaml"
    else:
        batch = yaml.safe_load(path.read_text())
        manifest_path = config_root() / "preservation/generated" / batch["batch_key"] / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text())
    sources: list[str] = []
    for game in manifest.get("games") or []:
        cfg = Path(game["config"])
        if not cfg.is_absolute():
            cfg = config_root() / "preservation/generated" / manifest["batch_key"] / cfg.name
            if not cfg.is_file():
                cfg = ROOT / game["config"]
        sources.append(source_path_from_game_config(cfg))
    conn = pg_connect()
    n = 0
    with conn.cursor() as cur:
        for source in sources:
            cur.execute(
                """
                UPDATE game_ingest SET
                    status = 'pending',
                    dip_path = NULL,
                    aip_bag_path = NULL,
                    omeka_item_id = NULL,
                    omeka_media_id = NULL,
                    moby_game_id = NULL,
                    moby_title = NULL,
                    metadata_json = NULL,
                    metadata_source = NULL,
                    moby_status = NULL,
                    block_reason = NULL,
                    package_storage_prefix = NULL,
                    aip_wasabi_prefix = NULL,
                    storage_layout = NULL,
                    updated_at = NOW()
                WHERE source_path = %s
                """,
                (source,),
            )
            n += cur.rowcount
    conn.commit()
    conn.close()
    print(f"Reset {n} ledger rows for {len(sources)} manifest games ({manifest['batch_key']})")


if __name__ == "__main__":
    main()
