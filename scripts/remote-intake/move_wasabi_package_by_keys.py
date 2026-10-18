#!/usr/bin/env python3
"""MOVE one Wasabi package tree using per-object Wasabi MOVE (fast rename)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from move_incoming_redump_packages import make_move_client  # noqa: E402
from upload_bag_wasabi import load_wasabi_settings  # noqa: E402
from wasabi_rename import wasabi_move_key, wasabi_move_prefix  # noqa: E402
from yaml_config import load_yaml  # noqa: E402

DEST_ROOT = "processed"


def list_file_keys(client, bucket: str, prefix: str) -> list[str]:
    root = prefix.strip("/") + "/"
    keys: list[str] = []
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=root):
        for obj in page.get("Contents") or []:
            key = str(obj["Key"])
            if key == root or key.endswith("/"):
                continue
            keys.append(key)
    return sorted(keys)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_prefix", help="e.g. GameWaveDVD/G_WAVE_011")
    parser.add_argument(
        "--intake-config",
        type=Path,
        default=Path("/tmp/wasabi-redump-intake.yaml"),
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--try-prefix-first", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    doc = load_yaml(args.intake_config)
    cfg = doc.get("wasabi_redump_intake") or {}
    config_root = args.intake_config.resolve().parent.parent
    from move_incoming_redump_packages import resolve_config_path  # noqa: E402

    ingest_path = resolve_config_path(str(cfg["ingest_config_file"]), config_root)
    wasabi, _ = load_wasabi_settings(ingest_path)
    cred_path = resolve_config_path(str(cfg["credentials_file"]), config_root)
    if not cred_path.is_file():
        cred_path = Path("/config/secrets/wasabi-credentials.yaml")
    creds = load_yaml(cred_path)
    client = make_move_client(wasabi, creds, cfg)
    bucket = str(cfg["source"]["bucket"])

    src = args.source_prefix.strip("/")
    dest = f"{DEST_ROOT}/{src}"
    keys = list_file_keys(client, bucket, src)
    if not keys:
        print(f"No objects under {src}/ (already moved?)")
        return

    print(f"Package {src}/ → {dest}/ ({len(keys)} object(s))")
    for k in keys:
        print(f"  {k}")

    if not args.execute:
        print("Dry-run. Re-run with --execute.")
        return

    if args.try_prefix_first:
        try:
            result = wasabi_move_prefix(
                client,
                bucket=bucket,
                source_prefix=src,
                destination_prefix=dest,
                overwrite=args.overwrite,
                quiet=True,
            )
            count = int(result.get("move_count") or 0)
            if count and not result.get("errors"):
                print(f"Prefix MOVE ok: {count} key(s)")
                left = list_file_keys(client, bucket, src)
                print(f"Done. source_remaining={len(left)} dest={len(list_file_keys(client, bucket, dest))}")
                if left:
                    raise SystemExit(1)
                return
            print("Prefix MOVE did not complete; falling back to per-key MOVE")
        except Exception as e:
            print(f"Prefix MOVE failed: {e}; per-key MOVE")

    for key in keys:
        rel = key[len(src) + 1 :]
        dest_key = f"{dest}/{rel}"
        print(f"MOVE {rel}…", flush=True)
        wasabi_move_key(
            client,
            bucket=bucket,
            source_key=key,
            destination_key=dest_key,
            overwrite=args.overwrite,
        )

    left = list_file_keys(client, bucket, src)
    at_dest = list_file_keys(client, bucket, dest)
    print(f"Done. source_remaining={len(left)} dest={len(at_dest)}")
    if left:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
