#!/usr/bin/env python3
"""Move one finished Wasabi intake package to processed/{lane}/ in the incoming bucket.

    {source_prefix}/{package}/  ->  processed/{lane}/{package}/

Server-side Wasabi MOVE, verified (every object at the destination with the same size, nothing
left at the source; see wasabi_rename.wasabi_move_prefix_verified). Idempotent: already moved is
success. Refuses to merge into an existing destination (a name collision needs a human).
Called by run-wasabi-redump-per-folder.sh once the ledger says complete.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from upload_bag_wasabi import load_wasabi_settings, make_s3_client  # noqa: E402
from wasabi_rename import WasabiMoveIncomplete, list_keys_with_sizes, wasabi_move_prefix_verified  # noqa: E402
from yaml_config import load_yaml  # noqa: E402

LANES = ("redump", "no-intro", "others")


def plan(source_prefix: str, package: str, lane: str) -> tuple[str, str]:
    if lane not in LANES:
        raise ValueError(f"unknown lane {lane!r}")
    name = package.strip().strip("/")
    if not name or "/" in name or name in (".", ".."):
        raise ValueError(f"bad package folder name {package!r}")
    parent = source_prefix.strip().strip("/")
    if parent.split("/", 1)[0].lower() == "processed":
        raise ValueError(f"source is already under processed/: {parent!r}")
    src = f"{parent}/{name}" if parent else name
    return src, f"processed/{lane}/{name}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bucket", required=True)
    ap.add_argument("--source-prefix", default="")
    ap.add_argument("--package-folder-name", required=True)
    ap.add_argument("--lane", required=True, choices=LANES)
    args = ap.parse_args()
    src, dst = plan(args.source_prefix, args.package_folder_name, args.lane)

    ingest_path = Path("/config/preservation/ingest.yaml")
    wasabi, _ = load_wasabi_settings(ingest_path)
    creds = load_yaml(Path("/config") / str(wasabi["credentials_file"]).removeprefix("config/"))
    s3 = make_s3_client(wasabi, creds)

    at_src = list_keys_with_sizes(s3, bucket=args.bucket, prefix=src)
    at_dst = list_keys_with_sizes(s3, bucket=args.bucket, prefix=dst)
    if not at_src and at_dst:
        print(json.dumps({"status": "already_processed", "to": dst, "objects": len(at_dst)}))
        return 0
    if not at_src:
        print(f"nothing at s3://{args.bucket}/{src}/", file=sys.stderr)
        return 1
    if at_dst:
        print(f"refusing: s3://{args.bucket}/{dst}/ already has {len(at_dst)} objects (name collision)", file=sys.stderr)
        return 1
    try:
        result = wasabi_move_prefix_verified(s3, bucket=args.bucket, source_prefix=src, destination_prefix=dst)
    except WasabiMoveIncomplete as e:
        print(f"move incomplete: {e}", file=sys.stderr)
        return 1
    print(json.dumps({"status": "moved", "from": src, "to": dst, "objects": result.get("verified_objects")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
