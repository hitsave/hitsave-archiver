#!/usr/bin/env python3
"""Which intake source does ready/<slug>/ belong to? (slug-collision guard)

ready/<slug> names come from slugified folder names with the source root dropped, so
different packages can map to the same slug (e.g. ``FamitsuWaveDVD/FWD0307`` and
``F.Wave/FWD0307``, or ``Game (USA)`` and ``Game [USA]``). The handoff manifest that
intake writes (hitsave-submission.yaml) records the real source: a Drive folder id
or a Wasabi ``<source_prefix>/<package_folder>``. Compare against it before treating a
slug as "already staged / already preserved".

The ledger copy (game_ingest.intake_source, see scripts/intake_source.py) is authoritative: when
the caller passes it with --ledger-source, it decides and ready/ isn't read at all, so the guard
keeps working after ready/ is pruned, lost, or the intake moves to another host. Without it (no
ledger row yet, or a row from before intake_source existed) the handoff manifest decides.

CLI (used by run-*-redump-per-folder.sh):
    ready_source.py check --slug SLUG (--drive-folder-id ID | --wasabi-prefix PREFIX)
        [--ledger-source REF] [--ready-root DIR]
    prints one of: absent | match | mismatch <recorded> | unknown
    exit 0 absent/match, 3 mismatch, 4 unknown (manifest records no source)
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from intake_source import (  # noqa: E402
    MANIFEST_NAME,
    current_ref,
    norm_prefix,
    parse_ref,
    recorded_source,
    to_ref,
)

ABSENT = "absent"      # no ready/<slug> handoff at all
MATCH = "match"        # handoff came from this same source
MISMATCH = "mismatch"  # handoff came from a DIFFERENT source -> slug collision
UNKNOWN = "unknown"    # handoff exists but records no source (legacy)


def default_ready_root() -> Path:
    """ready/ as seen from host, Windmill worker, ingest-worker or intake containers."""
    env = os.environ.get("HITSAVE_READY_ROOT", "").strip()
    if env:
        return Path(env)
    host = (os.environ.get("HOST_INTAKE") or os.environ.get("HOST_GDRIVE_INTAKE", "")).strip()
    candidates = []
    if host:
        candidates.append(Path(host) / "ready")
    candidates += [Path("/data/intake/ready")]
    for c in candidates:
        if c.is_dir():
            return c
    return candidates[0]


def require_ready_root(path: Path | None = None) -> Path:
    """default_ready_root(), but fail closed: an unmounted ready/ must not read as 'absent'."""
    root = path or default_ready_root()
    if not root.is_dir():
        raise SystemExit(
            f"ready/ not found at {root}; cannot run the slug-collision check "
            "(set HITSAVE_READY_ROOT or mount the intake ready/ directory)"
        )
    return root


_norm_prefix = norm_prefix  # kept for callers/tests


def describe(rec: dict | None) -> str:
    return to_ref(rec) or "(no source recorded)"


def check_ledger_source(
    ledger_source: str | None,
    *,
    drive_folder_id: str | None = None,
    wasabi_prefix: str | None = None,
) -> tuple[str, dict] | None:
    """(MATCH|MISMATCH, recorded) from the ledger's intake_source; None when it can't decide."""
    rec = parse_ref(ledger_source)
    if rec is None:
        return None
    current = current_ref(drive_folder_id=drive_folder_id, wasabi_prefix=wasabi_prefix)
    return (MATCH if to_ref(rec) == current else MISMATCH), rec


def check_source(
    ready_dir: Path | None,
    *,
    drive_folder_id: str | None = None,
    wasabi_prefix: str | None = None,
    ledger_source: str | None = None,
) -> tuple[str, dict | None]:
    if not drive_folder_id and not wasabi_prefix:
        raise ValueError("pass drive_folder_id or wasabi_prefix")
    decided = check_ledger_source(
        ledger_source, drive_folder_id=drive_folder_id, wasabi_prefix=wasabi_prefix
    )
    if decided is not None:
        return decided
    if ready_dir is None:
        raise ValueError("no usable ledger source and no ready dir to check")
    rec = recorded_source(ready_dir)
    if rec is None:
        return ABSENT, None
    if not rec:
        return UNKNOWN, rec
    if drive_folder_id:
        same = rec.get("drive_folder_id") == str(drive_folder_id).strip()
    else:
        same = rec.get("wasabi_prefix") == _norm_prefix(wasabi_prefix)
    return (MATCH if same else MISMATCH), rec


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    chk = sub.add_parser("check")
    chk.add_argument("--slug", required=True)
    src = chk.add_mutually_exclusive_group(required=True)
    src.add_argument("--drive-folder-id")
    src.add_argument("--wasabi-prefix")
    chk.add_argument("--ready-root", type=Path)
    chk.add_argument(
        "--ledger-source",
        default="",
        help="game_ingest.intake_source for this slug (drive:<id> / wasabi:<prefix>); decides when set",
    )
    args = parser.parse_args(argv)

    decided = check_ledger_source(
        args.ledger_source,
        drive_folder_id=args.drive_folder_id,
        wasabi_prefix=args.wasabi_prefix,
    )
    if decided is not None:
        state, rec = decided
    else:
        ready_root = require_ready_root(args.ready_root)
        state, rec = check_source(
            ready_root / args.slug,
            drive_folder_id=args.drive_folder_id,
            wasabi_prefix=args.wasabi_prefix,
        )
    if state == MISMATCH:
        print(f"{state} {describe(rec)}")
        return 3
    print(state)
    return 4 if state == UNKNOWN else 0


if __name__ == "__main__":
    sys.exit(main())
