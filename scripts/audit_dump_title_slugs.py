#!/usr/bin/env python3
"""Find dump package_ids whose title_slug lost macron romanization (legacy slug algorithm)."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

try:
    import psycopg
except ImportError as e:
    print(f"Missing dependency: {e}", file=sys.stderr)
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from package_identity import (  # noqa: E402
    parse_package_id,
    title_slug,
    title_slug_legacy_no_transliteration,
    transliterate_for_slug,
)
from yaml_config import load_yaml  # noqa: E402

_READY_CONTAINER = "/data/gdrive-intake/ready"
_MACRON_HINT = re.compile(r"[\u0100-\u024f]")  # Latin extended (macrons, etc.)


def pg_connect(db_cfg: dict):
    return psycopg.connect(
        host=db_cfg["host"],
        port=int(db_cfg.get("port", 5432)),
        dbname=db_cfg["database"],
        user=db_cfg["user"],
        password=db_cfg["password"],
    )


def host_ready_root() -> Path | None:
    host = os.environ.get("HOST_GDRIVE_INTAKE", "").strip()
    if not host:
        return None
    return Path(host) / "ready"


def catalog_title_for_row(source_path: str, ready_root: Path | None) -> str | None:
    if not ready_root or not source_path.startswith(_READY_CONTAINER):
        return None
    rel = source_path[len(_READY_CONTAINER) :].strip("/")
    if not rel:
        return None
    pkg_dir = ready_root / rel.split("/")[0]
    for name in ("game.ingest.yaml", "hitsave-submission.yaml"):
        path = pkg_dir / name
        if path.is_file():
            data = load_yaml(path)
            title = str(data.get("omeka_item_title") or "").strip()
            if title:
                return title
    return None


def main() -> None:
    report_path: Path | None = None
    argv = sys.argv[1:]
    if "--report" in argv:
        idx = argv.index("--report")
        report_path = Path(argv[idx + 1])
        argv = argv[:idx] + argv[idx + 2 :]

    ingest = load_yaml(Path("/config/preservation/ingest.yaml"))
    db_cfg = load_yaml(Path(ingest["database"]["config_file"]))["postgres"]
    ready_root = host_ready_root()

    with pg_connect(db_cfg) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT game_key, source_path, title_slug, package_storage_prefix, sort_letter
                FROM game_ingest
                WHERE ingest_kind = 'dump' AND status = 'complete'
                ORDER BY game_key
                """
            )
            rows = cur.fetchall()

    mangled: list[dict] = []
    ok_fold_would_match: list[str] = []
    no_title: list[str] = []

    for game_key, source_path, ledger_slug, prefix, sort_letter in rows:
        slug_from_key, _uid = parse_package_id(game_key)
        title = catalog_title_for_row(str(source_path or ""), ready_root)
        entry = {
            "game_key": game_key,
            "ledger_title_slug": ledger_slug or slug_from_key,
            "package_storage_prefix": prefix,
            "sort_letter": sort_letter,
            "source_path": source_path,
        }
        if not title:
            no_title.append(game_key)
            continue
        entry["omeka_item_title"] = title
        legacy = title_slug_legacy_no_transliteration(title)
        folded = title_slug(title)
        entry["legacy_slug"] = legacy
        entry["folded_slug"] = folded
        has_macron = bool(_MACRON_HINT.search(title)) or any(
            ord(c) > 127 for c in transliterate_for_slug(title) if c.isalpha()
        )
        if legacy != folded and (ledger_slug or slug_from_key) == legacy:
            entry["reason"] = "macron_stripped_in_legacy_slug"
            mangled.append(entry)
        elif legacy != folded and (ledger_slug or slug_from_key) == folded:
            ok_fold_would_match.append(game_key)

    summary = {
        "total_complete_dumps": len(rows),
        "mangled_macron_slug_count": len(mangled),
        "already_folded_slug_count": len(ok_fold_would_match),
        "no_local_title_yaml": len(no_title),
        "mangled": mangled,
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if mangled:
        print(
            f"\n{len(mangled)} dump(s) use legacy macron-mangled slugs (Wasabi MOVE + re-id candidate).",
            file=sys.stderr,
        )
        for item in mangled:
            print(
                f"  {item['game_key'][:60]}… legacy={item['legacy_slug']} → folded={item['folded_slug']}",
                file=sys.stderr,
            )


if __name__ == "__main__":
    main()
