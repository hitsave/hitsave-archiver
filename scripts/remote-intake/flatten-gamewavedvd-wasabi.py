#!/usr/bin/env python3
"""One-time: flatten GameWaveDVD wrappers on hitsave-incoming.

For each outer folder under GameWaveDVD/ that contains exactly one G_WAVE* child:
  1. Move loose objects at the outer level into that inner folder.
  2. MOVE the inner prefix to GameWaveDVD/{inner_name}/ (package root).
  3. Remove leftover zero-byte folder placeholders under the old outer prefix.
     Real objects are never deleted: any error or leftover object stops that package.

Already-flat G_WAVE* folders at GameWaveDVD/ top level are left unchanged.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import boto3
from botocore.config import Config

from move_incoming_redump_packages import (  # noqa: E402
    list_child_prefixes,
    make_move_client,
    resolve_config_path,
)
from upload_bag_wasabi import load_wasabi_settings  # noqa: E402
from wasabi_rename import WasabiMoveIncomplete, wasabi_move_prefix_verified  # noqa: E402
from yaml_config import load_yaml  # noqa: E402

G_WAVE_NAME = re.compile(r"^G_WAVE", re.I)
ROOT_PREFIX = "GameWaveDVD"


def list_loose_files(client, bucket: str, prefix_path: str) -> list[str]:
    """Object keys directly under prefix_path (not in subfolders)."""
    root = prefix_path.strip("/") + "/"
    keys: list[str] = []
    for page in client.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Prefix=root, Delimiter="/"
    ):
        for obj in page.get("Contents") or []:
            key = str(obj["Key"])
            if key == root or key.endswith("/"):
                continue
            rel = key[len(root) :]
            if rel and "/" not in rel:
                keys.append(key)
    return sorted(keys)


def prefix_has_objects(client, bucket: str, prefix: str) -> bool:
    root = prefix.strip("/") + "/"
    resp = client.list_objects_v2(Bucket=bucket, Prefix=root, MaxKeys=1)
    return bool(resp.get("Contents"))


def delete_folder_placeholders(client, bucket: str, prefix: str, *, dry_run: bool) -> int:
    """
    Delete only zero-byte "folder/" placeholder keys under prefix/.

    Refuses (raises) if any real object is still there: the old version deleted every
    leftover key, including objects whose copy or MOVE had failed (contributor originals).
    """
    root = prefix.strip("/") + "/"
    placeholders: list[str] = []
    leftovers: list[str] = []
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=root):
        for o in page.get("Contents") or []:
            key = str(o["Key"])
            if key.endswith("/") and int(o.get("Size") or 0) == 0:
                placeholders.append(key)
            else:
                leftovers.append(key)
    if leftovers:
        raise RuntimeError(
            f"{len(leftovers)} real object(s) still under {root} (not deleting): {leftovers[:5]}"
        )
    if dry_run or not placeholders:
        return len(placeholders)
    for i in range(0, len(placeholders), 1000):
        chunk = placeholders[i : i + 1000]
        client.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": k} for k in chunk]})
    return len(placeholders)


def move_object(client, bucket: str, source_key: str, dest_key: str, *, dry_run: bool) -> None:
    """Copy, confirm the copy (size + ETag), then delete the source."""
    if source_key == dest_key:
        return
    if dry_run:
        print(f"    loose: {source_key} -> {dest_key}")
        return
    src = client.head_object(Bucket=bucket, Key=source_key)
    client.copy_object(
        Bucket=bucket,
        Key=dest_key,
        CopySource={"Bucket": bucket, "Key": source_key},
    )
    dst = client.head_object(Bucket=bucket, Key=dest_key)
    if int(dst["ContentLength"]) != int(src["ContentLength"]) or dst.get("ETag") != src.get("ETag"):
        raise RuntimeError(
            f"copy of {source_key} -> {dest_key} not verified "
            f"(size {dst['ContentLength']} vs {src['ContentLength']}, etag {dst.get('ETag')} vs {src.get('ETag')});"
            " source kept"
        )
    client.delete_object(Bucket=bucket, Key=source_key)


def plan_packages(client, bucket: str) -> tuple[list[str], list[tuple[str, str, list[str]]]]:
    """Return (skip_flat_names, hoist_plans) where hoist = (outer_name, inner_name, loose_keys)."""
    outers = list_child_prefixes(client, bucket, ROOT_PREFIX)
    top = set(outers)
    skip: list[str] = []
    hoist: list[tuple[str, str, list[str]]] = []

    for outer in sorted(outers):
        outer_path = f"{ROOT_PREFIX}/{outer}"
        child_dirs = list_child_prefixes(client, bucket, outer_path)
        wave_dirs = [d for d in child_dirs if G_WAVE_NAME.match(d)]
        other_dirs = [d for d in child_dirs if not G_WAVE_NAME.match(d)]

        if not child_dirs and G_WAVE_NAME.match(outer):
            skip.append(outer)
            continue

        if len(wave_dirs) != 1 or other_dirs:
            raise SystemExit(
                f"Cannot flatten {outer_path!r}: G_WAVE dirs={wave_dirs!r}, other={other_dirs!r}"
            )

        inner = wave_dirs[0]
        if inner in top and inner != outer:
            raise SystemExit(
                f"Destination {ROOT_PREFIX}/{inner}/ already exists; refusing to hoist {outer!r}"
            )

        loose = list_loose_files(client, bucket, outer_path)
        hoist.append((outer, inner, loose))

    return skip, hoist


def run(
    client,
    bucket: str,
    *,
    dry_run: bool,
    overwrite: bool,
) -> dict[str, int]:
    stats = {
        "skipped_flat": 0,
        "hoisted": 0,
        "loose_moved": 0,
        "prefix_objects_moved": 0,
        "outer_keys_deleted": 0,
        "errors": 0,
    }

    skip, hoist = plan_packages(client, bucket)
    stats["skipped_flat"] = len(skip)
    for name in skip:
        print(f"SKIP (already flat): {ROOT_PREFIX}/{name}/")

    for outer, inner, loose_keys in hoist:
        outer_path = f"{ROOT_PREFIX}/{outer}"
        inner_under_outer = f"{outer_path}/{inner}"
        dest_root = f"{ROOT_PREFIX}/{inner}"
        print(f"\n{'DRY-RUN' if dry_run else 'FLATTEN'} {outer_path}/ -> {dest_root}/")

        package_ok = True
        for key in loose_keys:
            filename = key.split("/")[-1]
            dest_key = f"{inner_under_outer}/{filename}"
            try:
                move_object(client, bucket, key, dest_key, dry_run=dry_run)
                stats["loose_moved"] += 1
            except Exception as e:
                stats["errors"] += 1
                package_ok = False
                print(f"  ERROR moving loose {key}: {e}", file=sys.stderr)
        if not package_ok:
            print(f"  STOP {outer_path}/: loose-file errors; nothing else moved or deleted", file=sys.stderr)
            continue

        print(f"  {'would MOVE' if dry_run else 'MOVE'} prefix {inner_under_outer}/ -> {dest_root}/")
        if dry_run:
            resp = client.list_objects_v2(
                Bucket=bucket, Prefix=inner_under_outer.strip("/") + "/", MaxKeys=1000
            )
            stats["prefix_objects_moved"] += len(resp.get("Contents") or [])
        else:
            try:
                result = wasabi_move_prefix_verified(
                    client,
                    bucket=bucket,
                    source_prefix=inner_under_outer,
                    destination_prefix=dest_root,
                    overwrite=overwrite,
                )
            except (WasabiMoveIncomplete, Exception) as e:
                stats["errors"] += 1
                print(f"  ERROR prefix move (nothing deleted): {e}", file=sys.stderr)
                continue
            stats["prefix_objects_moved"] += int(result.get("verified_objects") or 0)
            print(f"  moved and verified {result.get('verified_objects')} object(s)")

        try:
            n_del = delete_folder_placeholders(client, bucket, outer_path, dry_run=dry_run)
        except RuntimeError as e:
            if dry_run:
                # Loose files and the inner prefix are still there in a dry run.
                n_del = 0
            else:
                stats["errors"] += 1
                print(f"  ERROR cleanup: {e}", file=sys.stderr)
                continue
        stats["outer_keys_deleted"] += n_del
        if n_del:
            print(f"  {'would delete' if dry_run else 'deleted'} {n_del} folder placeholder(s) under {outer_path}/")

        stats["hoisted"] += 1

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("/config/preservation/wasabi-redump-intake.yaml"),
        help="wasabi_redump_intake config (bucket + credentials paths)",
    )
    parser.add_argument("--execute", action="store_true", help="Apply changes (default: dry-run)")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow MOVE into an existing destination prefix (default: refuse)",
    )
    args = parser.parse_args()

    doc = load_yaml(args.config)
    cfg = doc.get("wasabi_redump_intake") or {}
    if not cfg:
        raise SystemExit(f"No wasabi_redump_intake in {args.config}")

    config_root = args.config.resolve().parent.parent
    ingest_path = resolve_config_path(str(cfg["ingest_config_file"]), config_root)
    wasabi, _ = load_wasabi_settings(ingest_path)
    cred_path = resolve_config_path(str(cfg["credentials_file"]), config_root)
    if not cred_path.is_file():
        fallback = Path("/config/secrets/wasabi-credentials.yaml")
        if fallback.is_file():
            cred_path = fallback
    if not cred_path.is_file():
        raise SystemExit(f"Wasabi credentials not found: {cred_path}")
    creds = load_yaml(cred_path)
    client = make_move_client(wasabi, creds, cfg)
    bucket = str(cfg["source"]["bucket"])

    stats = run(client, bucket, dry_run=not args.execute, overwrite=args.overwrite)
    print("\nSummary:", stats)
    if not args.execute:
        print("Dry-run only. Re-run with --execute to apply.")
    if stats["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
