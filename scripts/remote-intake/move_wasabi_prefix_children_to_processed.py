#!/usr/bin/env python3
"""MOVE each immediate child package under a Wasabi prefix into processed/{prefix}/{child}/."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from move_incoming_redump_packages import (  # noqa: E402
    list_child_prefixes,
    make_move_client,
    resolve_config_path,
)
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


def move_one_package(
    client,
    bucket: str,
    src: str,
    *,
    execute: bool,
    overwrite: bool,
    try_prefix_first: bool,
) -> tuple[bool, str]:
    dest = f"{DEST_ROOT}/{src}"
    keys = list_file_keys(client, bucket, src)
    if not keys:
        return True, "skip (empty at source)"

    if not execute:
        return True, f"dry-run {len(keys)} key(s)"

    if try_prefix_first:
        try:
            result = wasabi_move_prefix(
                client,
                bucket=bucket,
                source_prefix=src,
                destination_prefix=dest,
                overwrite=overwrite,
                quiet=True,
            )
            count = int(result.get("move_count") or 0)
            if count and not result.get("errors") and not list_file_keys(client, bucket, src):
                return True, f"prefix MOVE {count} key(s)"
        except Exception:
            pass

    for key in keys:
        rel = key[len(src) + 1 :]
        wasabi_move_key(
            client,
            bucket=bucket,
            source_key=key,
            destination_key=f"{dest}/{rel}",
            overwrite=overwrite,
        )
    left = list_file_keys(client, bucket, src)
    if left:
        return False, f"source still has {len(left)} key(s)"
    return True, f"per-key MOVE {len(keys)} key(s)"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("parent_prefix", help="e.g. redump-from-tony")
    parser.add_argument("--intake-config", type=Path, default=Path("/tmp/wasabi-redump-intake.yaml"))
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--try-prefix-first", action="store_true")
    args = parser.parse_args()

    doc = load_yaml(args.intake_config)
    cfg = doc.get("wasabi_redump_intake") or {}
    config_root = args.intake_config.resolve().parent.parent
    ingest_path = resolve_config_path(str(cfg["ingest_config_file"]), config_root)
    wasabi, _ = load_wasabi_settings(ingest_path)
    cred_path = resolve_config_path(str(cfg["credentials_file"]), config_root)
    if not cred_path.is_file():
        cred_path = Path("/config/secrets/wasabi-credentials.yaml")
    creds = load_yaml(cred_path)
    client = make_move_client(wasabi, creds, cfg)
    bucket = str(cfg["source"]["bucket"])

    parent = args.parent_prefix.strip("/")
    children = list_child_prefixes(client, bucket, parent)
    print(f"Parent {parent}/: {len(children)} package(s)")

    ok = fail = 0
    for child in children:
        src = f"{parent}/{child}"
        good, msg = move_one_package(
            client,
            bucket,
            src,
            execute=args.execute,
            overwrite=args.overwrite,
            try_prefix_first=args.try_prefix_first,
        )
        mark = "ok" if good else "FAIL"
        print(f"  [{mark}] {src}: {msg}")
        if good:
            ok += 1
        else:
            fail += 1

    print(f"\nSummary: ok={ok} fail={fail} total={len(children)}")
    if not args.execute:
        print("Dry-run only. Re-run with --execute.")
    if fail:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
