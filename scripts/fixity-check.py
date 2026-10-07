#!/usr/bin/env python3
"""Sample fixity check against ledger E-ARK AIP directories (checksum sidecar + METS)."""
from __future__ import annotations

import re
import sys
from pathlib import Path

try:
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


def verify_aip(aip_dir: Path, sample_percent: int) -> tuple[str, str, int]:
    root_mets = aip_dir / "METS.xml"
    if not root_mets.is_file():
        return "fail", f"missing METS.xml under {aip_dir}", 0
    sidecar = aip_dir / "metadata" / "other" / "checksums-sha256.txt"
    if not sidecar.is_file():
        return "fail", "missing metadata/other/checksums-sha256.txt", 0
    rep_data = None
    for candidate in aip_dir.glob("representations/*/data"):
        if candidate.is_dir():
            rep_data = candidate
            break
    if rep_data is None:
        return "fail", "no representations/*/data directory", 0

    lines = [
        ln.strip()
        for ln in sidecar.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.startswith("#")
    ]
    if not lines:
        return "fail", "empty checksum sidecar", 0

    checked = 0
    step = max(1, int(100 / sample_percent)) if sample_percent < 100 else 1
    for i, line in enumerate(lines):
        if sample_percent < 100 and i % step != 0:
            continue
        rel, expected = line.split("\t", 1)
        path = rep_data / rel
        if not path.is_file():
            return "fail", f"missing data file {rel}", checked
        actual = sha256_file(path)
        if actual.lower() != expected.lower():
            return "fail", f"digest mismatch for {rel}", checked
        checked += 1

    text = root_mets.read_text(encoding="utf-8", errors="replace")
    if not re.search(r'\bOBJID="[^"]+"', text):
        return "fail", "METS.xml missing OBJID", checked

    return "pass", f"E-ARK AIP spot-check ok ({checked} files, sample_percent={sample_percent})", checked


def main() -> None:
    sample_percent = 100
    if Path("/config/preservation/fixity.yaml").is_file():
        fixity_cfg = yaml.safe_load(Path("/config/preservation/fixity.yaml").read_text()) or {}
        sample_percent = int(fixity_cfg.get("sample_percent", 100))

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
                SELECT id, game_key, aip_bag_path
                FROM game_ingest
                WHERE status = 'complete' AND aip_bag_path IS NOT NULL
                """
            )
            rows = cur.fetchall()

    checked_bags = 0
    for ingest_id, game_key, aip_path in rows:
        aip_dir = Path(aip_path)
        if not aip_dir.is_dir():
            print(f"SKIP {game_key}: missing AIP {aip_dir}")
            continue
        outcome, detail, files_checked = verify_aip(aip_dir, sample_percent)
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

    print(f"Fixity run finished ({checked_bags} AIPs)")


if __name__ == "__main__":
    main()
