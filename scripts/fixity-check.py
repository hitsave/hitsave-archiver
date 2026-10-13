#!/usr/bin/env python3
"""Sample fixity check against ledger BagIt directories (RFC 8493 manifests + inventory)."""
from __future__ import annotations

import sys
from pathlib import Path

try:
    import bagit
    import psycopg
    import yaml
except ImportError as e:
    print(f"Missing dependency: {e}", file=sys.stderr)
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from preservation_common import sha256_file  # noqa: E402


def load_db() -> dict:
    ingest = yaml.safe_load(Path("/config/preservation/ingest.yaml").read_text())
    db_path = Path(ingest["database"]["config_file"])
    return yaml.safe_load(db_path.read_text())["postgres"]


def verify_bagit_bag(bag_dir: Path, sample_percent: int) -> tuple[str, str, int]:
    bagit_txt = bag_dir / "bagit.txt"
    manifest = bag_dir / "manifest-sha256.txt"
    if not bagit_txt.is_file():
        return "fail", f"missing bagit.txt under {bag_dir}", 0
    if not manifest.is_file():
        return "fail", "missing manifest-sha256.txt", 0

    try:
        bag = bagit.Bag(str(bag_dir))
        bag.validate()
    except Exception as e:
        return "fail", f"bag.validate() failed: {e}", 0

    inv_path = bag_dir / "metadata" / "inventory.json"
    if not inv_path.is_file():
        return "pass", "BagIt validate ok (no metadata/inventory.json to spot-check)", 0

    import json

    inventory = json.loads(inv_path.read_text(encoding="utf-8"))
    files = inventory.get("files") if isinstance(inventory, dict) else None
    if not isinstance(files, list) or not files:
        return "pass", "BagIt validate ok (empty inventory)", 0

    checked = 0
    step = max(1, int(100 / sample_percent)) if sample_percent < 100 else 1
    for i, rec in enumerate(files):
        if sample_percent < 100 and i % step != 0:
            continue
        if not isinstance(rec, dict):
            continue
        rel = str(rec.get("path") or "").lstrip("/")
        expected = str(rec.get("sha256") or "").lower()
        if not rel or not expected:
            continue
        path = bag_dir / rel
        if not path.is_file():
            return "fail", f"missing payload file {rel}", checked
        actual = sha256_file(path).lower()
        if actual != expected:
            return "fail", f"digest mismatch for {rel}", checked
        checked += 1

    return (
        "pass",
        f"BagIt spot-check ok ({checked} files, sample_percent={sample_percent})",
        checked,
    )


def resolve_bag_dir(row: tuple) -> Path | None:
    """Prefer aip_bag_path, then dip_path when it points at a bag directory."""
    _ingest_id, _game_key, dip_path, aip_bag_path = row
    for candidate in (aip_bag_path, dip_path):
        if not candidate:
            continue
        path = Path(candidate)
        if path.is_dir() and (path / "bagit.txt").is_file():
            return path
    return None


def main() -> None:
    sample_percent = 100
    for fixity_path in (
        Path("/config/preservation/fixity.yaml"),
        Path(__file__).resolve().parents[1] / "config" / "preservation" / "fixity.yaml",
    ):
        if fixity_path.is_file():
            fixity_cfg = yaml.safe_load(fixity_path.read_text()) or {}
            sample_percent = int(fixity_cfg.get("sample_percent", 100))
            break

    db = load_db()
    with psycopg.connect(
        host=db["host"],
        port=int(db.get("port", 5432)),
        dbname=db["database"],
        user=db["user"],
        password=db["password"],
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, game_key, dip_path, aip_bag_path
                FROM game_ingest
                WHERE status = 'complete'
                  AND (aip_bag_path IS NOT NULL OR dip_path IS NOT NULL)
                """
            )
            rows = cur.fetchall()

    checked_bags = 0
    for row in rows:
        ingest_id, game_key = row[0], row[1]
        bag_dir = resolve_bag_dir(row)
        if bag_dir is None:
            print(f"SKIP {game_key}: no local BagIt directory")
            continue
        outcome, detail, files_checked = verify_bagit_bag(bag_dir, sample_percent)
        checked_bags += 1
        with psycopg.connect(
            host=db["host"],
            port=int(db.get("port", 5432)),
            dbname=db["database"],
            user=db["user"],
            password=db["password"],
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO fixity_run (game_ingest_id, outcome, files_checked, detail)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (ingest_id, outcome, files_checked, detail),
                )
            conn.commit()
        print(f"{game_key}: {outcome} — {detail}")

    print(f"Fixity run finished ({checked_bags} bags)")


if __name__ == "__main__":
    main()
