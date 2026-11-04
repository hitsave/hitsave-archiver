#!/usr/bin/env python3
"""Mirror intake's failed/ folder into the ledger table intake_rejection.

Intake rejects a package before it reaches the ledger (checksum mismatch, missing sidecar, wrong
lane) and leaves it in failed/<slug>/ with an error.txt. That folder only exists on the host that
runs intake, so the ledger app (which may run elsewhere) reads this table instead.

Run at the end of each intake sweep, in the ingest-worker image:
  -v "$HOST_INTAKE:/data/intake:ro" ... python sync_intake_rejections.py --failed-dir /data/intake/failed
The table then holds exactly the packages currently in failed/. If failed/ isn't there, the script
changes nothing (it never empties the table because of a missing folder or mount).
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batch_ledger import pg_connect  # noqa: E402

REASON_MAX = 300
LANE_PREFIXES = {"redump-": "redump", "nointro-": "no-intro", "others-": "others"}

DDL = """
CREATE TABLE IF NOT EXISTS intake_rejection (
    slug        text PRIMARY KEY,
    lane        text,
    reason      text NOT NULL,
    failed_at   timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now()
)
"""


@dataclass
class Rejection:
    slug: str
    lane: str | None
    reason: str
    failed_at: datetime


def lane_for_slug(slug: str) -> str | None:
    for prefix, lane in LANE_PREFIXES.items():
        if slug.startswith(prefix):
            return lane
    return None


def scan(failed_dir: Path) -> list[Rejection]:
    """Every failed/<slug>/ folder: first line of error.txt and when it failed."""
    out: list[Rejection] = []
    for entry in sorted(failed_dir.iterdir()):
        if not entry.is_dir() or entry.is_symlink():
            continue
        err = entry / "error.txt"
        try:
            st = (err if err.is_file() else entry).stat()
            text = err.read_text(encoding="utf-8", errors="replace").strip() if err.is_file() else ""
        except OSError:
            continue
        reason = (text.splitlines()[0] if text else "(no error.txt)")[:REASON_MAX]
        out.append(Rejection(entry.name, lane_for_slug(entry.name), reason,
                             datetime.fromtimestamp(st.st_mtime, timezone.utc)))
    return out


def sync(conn, rejections: list[Rejection]) -> tuple[int, int]:
    """Make the table equal to rejections in one transaction. Returns (kept/updated, removed)."""
    slugs = [r.slug for r in rejections]
    with conn.transaction():
        conn.execute(DDL)
        removed = conn.execute(
            "DELETE FROM intake_rejection WHERE NOT (slug = ANY(%s))", (slugs,)
        ).rowcount
        for r in rejections:
            conn.execute(
                """INSERT INTO intake_rejection (slug, lane, reason, failed_at)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (slug) DO UPDATE
                   SET lane = EXCLUDED.lane, reason = EXCLUDED.reason, failed_at = EXCLUDED.failed_at,
                       recorded_at = CASE WHEN intake_rejection.failed_at = EXCLUDED.failed_at
                                          THEN intake_rejection.recorded_at ELSE now() END""",
                (r.slug, r.lane, r.reason, r.failed_at),
            )
    return len(rejections), removed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--failed-dir", type=Path, default=Path("/data/intake-failed"))
    args = ap.parse_args()
    if not args.failed_dir.is_dir():
        print(f"intake rejections: {args.failed_dir} not mounted; ledger table left unchanged", file=sys.stderr)
        return 0
    rejections = scan(args.failed_dir)
    with pg_connect() as conn:
        n, removed = sync(conn, rejections)
    print(f"intake rejections: {n} in failed/, {removed} removed from the ledger table")
    return 0


if __name__ == "__main__":
    sys.exit(main())
