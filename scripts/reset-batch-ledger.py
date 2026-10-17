#!/usr/bin/env python3
"""Clear Omeka IDs (and optional Moby) on ledger rows for a batch re-run."""
from __future__ import annotations

import sys
from pathlib import Path

try:
    import yaml
except ImportError as e:
    print(e, file=sys.stderr)
    sys.exit(1)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from batch_ledger import config_root, pg_connect, source_path_from_game_config  # noqa: E402
from batch_manifest import resolve_game_config_path, resolve_manifest_path  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: reset-batch-ledger.py <batch.yml|manifest.yaml>")
    raw = Path(sys.argv[1])
    if not raw.is_absolute() and not raw.is_file():
        candidate = config_root() / raw
        if candidate.is_file():
            raw = candidate
    cfg_root = config_root()
    manifest_path = resolve_manifest_path(raw, cfg_root)
    manifest = yaml.safe_load(manifest_path.read_text())
    sources: list[str] = []
    for game in manifest.get("games") or []:
        cfg = resolve_game_config_path(manifest, game, cfg_root=cfg_root, repo_root=ROOT)
        sources.append(source_path_from_game_config(cfg))
    conn = pg_connect()
    n = 0
    with conn.cursor() as cur:
        for source in sources:
            cur.execute(
                """
                UPDATE game_ingest SET
                    omeka_item_id = NULL,
                    omeka_media_id = NULL,
                    moby_game_id = NULL,
                    moby_title = NULL,
                    metadata_json = NULL,
                    metadata_source = NULL,
                    moby_status = NULL,
                    updated_at = NOW()
                WHERE source_path = %s
                """,
                (source,),
            )
            n += cur.rowcount
    conn.commit()
    conn.close()
    print(f"Cleared Omeka/Moby on {n} ledger rows ({manifest['batch_key']}, {len(sources)} games)")


if __name__ == "__main__":
    main()
