#!/usr/bin/env python3
"""Fill game_ingest.intake_source for dump packages ingested before the column existed.

Reads each package's handoff manifest (ready/<slug>/hitsave-submission.yaml, which pruning keeps)
and records its source in the ledger in canonical form (drive:<id> / wasabi:<prefix>), so the
remote-intake slug-collision guard no longer depends on the intake host's ready/ folder.

Only fills rows whose intake_source is empty; never overwrites. Dry run by default.

Run in the ingest-worker image (ledger config + intake ready/ mounted), e.g. on the intake host:
  docker compose run --rm -T -v "$HOST_INTAKE/ready:/data/intake/ready:ro" \
    --entrypoint python ingest-worker /app/scripts/backfill-intake-source.py [--execute]
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from intake_source import recorded_source, to_ref  # noqa: E402

DEFAULT_READY_ROOT = Path("/data/intake/ready")


@dataclass
class Planned:
    game_key: str
    slug: str
    intake_source: str | None  # None: nothing usable to record
    note: str


def plan(rows: list[tuple[str, str | None]], ready_root: Path) -> list[Planned]:
    """rows: (game_key, source_path) of dump rows with no intake_source."""
    out: list[Planned] = []
    for game_key, source_path in rows:
        slug = Path(str(source_path or "").rstrip("/")).name
        if not slug:
            out.append(Planned(game_key, "", None, "no source_path"))
            continue
        rec = recorded_source(ready_root / slug)
        if rec is None:
            out.append(Planned(game_key, slug, None, "no handoff manifest in ready/"))
        elif not rec:
            out.append(Planned(game_key, slug, None, "manifest records no source"))
        else:
            out.append(Planned(game_key, slug, to_ref(rec), "from manifest"))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ready-root", type=Path, default=DEFAULT_READY_ROOT)
    ap.add_argument("--execute", action="store_true", help="write to the ledger (default: dry run)")
    args = ap.parse_args(argv)
    if not args.ready_root.is_dir():
        raise SystemExit(f"ready/ not found at {args.ready_root}")

    from batch_ledger import pg_connect  # noqa: E402  (psycopg + ledger config, ingest-worker image)

    with pg_connect() as conn:
        rows = conn.execute(
            """SELECT game_key, source_path FROM game_ingest
               WHERE ingest_kind = 'dump' AND intake_source IS NULL
               ORDER BY game_key"""
        ).fetchall()
        planned = plan([(r[0], r[1]) for r in rows], args.ready_root)
        fill = [p for p in planned if p.intake_source]
        skip = [p for p in planned if not p.intake_source]
        for p in skip:
            print(f"skip {p.game_key} ({p.slug or '-'}): {p.note}")
        if args.execute:
            with conn.transaction():
                for p in fill:
                    conn.execute(
                        "UPDATE game_ingest SET intake_source = %s "
                        "WHERE game_key = %s AND intake_source IS NULL",
                        (p.intake_source, p.game_key),
                    )
        verb = "filled" if args.execute else "would fill"
        print(f"intake_source: {verb} {len(fill)} of {len(planned)} dump rows; {len(skip)} skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
