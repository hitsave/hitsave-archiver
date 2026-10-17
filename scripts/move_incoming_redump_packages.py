#!/usr/bin/env python3
"""Rename Redump SIP folder trees on Wasabi (Wasabi MOVE API, not CopyObject)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import boto3
from botocore.config import Config

from preservation_common import wasabi_s3_region  # noqa: E402
from upload_bag_wasabi import load_wasabi_settings  # noqa: E402
from wasabi_rename import wasabi_move_prefix  # noqa: E402
from yaml_config import load_yaml  # noqa: E402


def make_move_client(wasabi: dict, creds: dict, cfg: dict):
    s3_cfg = cfg.get("s3_client") or {}
    read_timeout = int(s3_cfg.get("read_timeout_seconds", 600))
    connect_timeout = int(s3_cfg.get("connect_timeout_seconds", 120))
    max_attempts = int(s3_cfg.get("max_attempts", 10))
    return boto3.client(
        "s3",
        endpoint_url=str(wasabi["endpoint"]).rstrip("/"),
        region_name=wasabi_s3_region(wasabi),
        aws_access_key_id=str(creds["access_key_id"]),
        aws_secret_access_key=str(creds["secret_access_key"]),
        config=Config(
            signature_version="s3v4",
            read_timeout=read_timeout,
            connect_timeout=connect_timeout,
            retries={"max_attempts": max_attempts, "mode": "adaptive"},
        ),
    )


def resolve_config_path(raw: str, config_root: Path) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else config_root / path


def list_child_prefixes(client, bucket: str, prefix: str) -> list[str]:
    root = prefix.strip("/") + "/"
    names: list[str] = []
    for page in client.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Prefix=root, Delimiter="/"
    ):
        for cp in page.get("CommonPrefixes") or []:
            name = cp["Prefix"][len(root) :].strip("/")
            if name:
                names.append(name)
    return sorted(names)


def list_top_level_prefixes(client, bucket: str, name_prefixes: list[str]) -> list[str]:
    found: list[str] = []
    for page in client.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Delimiter="/"
    ):
        for cp in page.get("CommonPrefixes") or []:
            name = cp["Prefix"].strip("/")
            if any(name.startswith(p) for p in name_prefixes):
                found.append(name)
    return sorted(found)


def plan_moves(client, cfg: dict) -> list[tuple[str, str]]:
    bucket = str(cfg["bucket"])
    dest_root = str(cfg["dest_prefix"]).strip("/")
    planned: list[tuple[str, str]] = []
    seen_dest: set[str] = set()

    def add_src_prefix(src_prefix: str) -> None:
        src = src_prefix.strip("/")
        if not src or src == dest_root or src.startswith(dest_root + "/"):
            return
        pkg_name = src.split("/")[-1]
        dest = f"{dest_root}/{pkg_name}"
        if dest in seen_dest:
            return
        seen_dest.add(dest)
        planned.append((src, dest))

    for source in cfg.get("sources") or []:
        kind = str(source.get("kind") or "")
        if kind == "child_packages":
            parent = str(source["prefix"])
            for child in list_child_prefixes(client, bucket, parent):
                add_src_prefix(f"{parent.strip('/')}/{child}")
        elif kind == "top_level_packages":
            prefixes = [str(p) for p in source.get("name_prefixes") or []]
            for name in list_top_level_prefixes(client, bucket, prefixes):
                add_src_prefix(name)
        else:
            raise ValueError(f"Unknown source kind {kind!r}")

    return planned


def dest_prefix_has_objects(client, bucket: str, dest: str) -> bool:
    prefix = dest.strip("/") + "/"
    resp = client.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=1)
    return bool(resp.get("Contents") or resp.get("CommonPrefixes"))


def run_moves(
    client,
    bucket: str,
    moves: list[tuple[str, str]],
    *,
    dry_run: bool,
    overwrite: bool,
) -> dict[str, int]:
    stats = {
        "packages_renamed": 0,
        "objects_moved": 0,
        "skipped_dest_exists": 0,
        "errors": 0,
    }
    rename_cfg = {}
    for src, dest in moves:
        print(f"\n{'DRY-RUN' if dry_run else 'RENAME'} {src}/ -> {dest}/ (Wasabi MOVE prefix)")
        if not dry_run and not overwrite and dest_prefix_has_objects(client, bucket, dest):
            stats["skipped_dest_exists"] += 1
            print(f"  skip: destination prefix already has objects ({dest}/)")
            continue
        if dry_run:
            keys = client.list_objects_v2(Bucket=bucket, Prefix=src.strip("/") + "/", MaxKeys=1000)
            n = len(keys.get("Contents") or [])
            stats["objects_moved"] += n
            stats["packages_renamed"] += 1
            print(f"  would move ~{n} object(s) under prefix")
            continue
        try:
            result = wasabi_move_prefix(
                client,
                bucket=bucket,
                source_prefix=src,
                destination_prefix=dest,
                overwrite=overwrite,
                quiet=True,
            )
        except Exception as e:
            stats["errors"] += 1
            print(f"  ERROR: {e}", file=sys.stderr)
            continue
        if result.get("errors"):
            stats["errors"] += len(result["errors"])
            for err in result["errors"]:
                print(f"  move error: {err}", file=sys.stderr)
        count = int(result.get("move_count") or 0)
        stats["objects_moved"] += count
        stats["packages_renamed"] += 1
        print(f"  moved {count} object key(s)")
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rename Redump packages on Wasabi using the MOVE API (prefix rename)."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("/config/preservation/wasabi-incoming-reorganize.yaml"),
    )
    parser.add_argument("--execute", action="store_true", help="Apply renames (default: dry-run)")
    args = parser.parse_args()

    doc = load_yaml(args.config)
    cfg = doc.get("wasabi_incoming_reorganize") or {}
    if not cfg:
        raise SystemExit(f"No wasabi_incoming_reorganize in {args.config}")

    rename_opts = cfg.get("rename") or {}
    overwrite = bool(rename_opts.get("overwrite", False))

    config_root = args.config.resolve().parent.parent
    ingest_path = resolve_config_path(str(cfg["ingest_config_file"]), config_root)
    wasabi, _ = load_wasabi_settings(ingest_path)
    cred_path = resolve_config_path(str(cfg["credentials_file"]), config_root)
    creds = load_yaml(cred_path)
    client = make_move_client(wasabi, creds, cfg)

    moves = plan_moves(client, cfg)
    print(f"Planned {len(moves)} package renames into {cfg['dest_prefix']}/")
    for src, dest in moves:
        print(f"  {src} -> {dest}")

    stats = run_moves(
        client,
        str(cfg["bucket"]),
        moves,
        dry_run=not args.execute,
        overwrite=overwrite,
    )
    print("\nSummary:", stats)
    if not args.execute:
        print("Dry-run only. Re-run with --execute to apply.")
    if stats["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
