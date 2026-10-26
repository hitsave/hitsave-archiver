#!/usr/bin/env python3
"""Compare poll inbox packages to Wasabi keys (incoming vs processed/) and ledger."""
#requirements:
#boto3
#psycopg[binary]
#pyyaml
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import boto3  # noqa: E402
import psycopg  # noqa: E402
import yaml  # noqa: E402

from inbox_rows_from_poll_json import wasabi_package_rows  # noqa: E402
from ledger_row_for_inbox import ledger_row_for_inbox_package  # noqa: E402
from preservation_common import wasabi_s3_region  # noqa: E402
from upload_bag_wasabi import load_wasabi_settings  # noqa: E402
from yaml_config import load_yaml  # noqa: E402

PROCESSED_ROOT = "processed"


def prefix_has_objects(s3, bucket: str, prefix: str) -> bool:
    p = prefix.strip("/") + "/"
    resp = s3.list_objects_v2(Bucket=bucket, Prefix=p, MaxKeys=1)
    return bool(resp.get("Contents") or resp.get("CommonPrefixes"))


def load_wasabi_intake(config_root: Path) -> dict:
    path = config_root / "preservation" / "wasabi-redump-intake.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return doc["wasabi_redump_intake"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--poll-json", type=Path, required=True)
    ap.add_argument("--config-root", type=Path, default=Path("/config"))
    ap.add_argument("--ingest-config", type=Path, default=Path("/config/preservation/ingest.yaml"))
    args = ap.parse_args()

    poll = json.loads(args.poll_json.read_text(encoding="utf-8"))
    bucket = str(poll.get("bucket") or load_wasabi_intake(args.config_root)["source"]["bucket"])
    packages = wasabi_package_rows(poll)

    wasabi, _ = load_wasabi_settings(args.ingest_config)
    creds = load_yaml(Path("/config/secrets/wasabi-credentials.yaml"))
    s3 = boto3.client(
        "s3",
        endpoint_url=str(wasabi["endpoint"]).rstrip("/"),
        region_name=wasabi_s3_region(wasabi),
        aws_access_key_id=creds["access_key_id"],
        aws_secret_access_key=creds["secret_access_key"],
    )

    db_cfg = load_yaml(Path(load_yaml(args.ingest_config)["database"]["config_file"]))["postgres"]
    conn = psycopg.connect(
        host=db_cfg["host"],
        port=int(db_cfg.get("port", 5432)),
        dbname=db_cfg["database"],
        user=db_cfg["user"],
        password=db_cfg["password"],
    )

    roots = {str(r.get("prefix") or "") for r in poll.get("source_roots") or []}
    print(f"bucket={bucket} poll_packages={len(packages)} source_roots={sorted(roots)}")
    print(f"poller prefixes (must not include {PROCESSED_ROOT}/): {sorted(roots)}")
    if PROCESSED_ROOT in roots or any(str(r).startswith(PROCESSED_ROOT) for r in roots):
        print("ERROR: source_roots includes processed/", file=sys.stderr)
        sys.exit(2)
    print()

    still_incoming: list[str] = []
    only_processed: list[str] = []
    neither: list[str] = []
    not_complete: list[str] = []

    with conn.cursor() as cur:
        for pkg in packages:
            slug = pkg["ready_slug"]
            s3_prefix = str(pkg.get("external_ref") or "").strip()
            if not s3_prefix:
                continue
            st, omeka = ledger_row_for_inbox_package(
                cur,
                ready_slug=slug,
                package_folder_name=pkg.get("package_folder_name"),
                stored_lookup_slugs=list(pkg.get("ledger_lookup_slugs") or []),
            )
            if st != "complete":
                not_complete.append(f"{slug} ledger={st or 'missing'}")
                continue
            inc = prefix_has_objects(s3, bucket, s3_prefix)
            proc = prefix_has_objects(s3, bucket, f"{PROCESSED_ROOT}/{s3_prefix}")
            line = f"{slug} omeka={omeka} incoming={s3_prefix!r} inc={inc} proc={proc}"
            if inc and not proc:
                still_incoming.append(line)
            elif proc and not inc:
                only_processed.append(line)
            elif inc and proc:
                still_incoming.append(line + " DUPLICATE_BOTH")
            else:
                neither.append(line)

    print("=== ledger complete, still under incoming prefix (not moved) ===")
    for line in still_incoming:
        print(line)
    print(f"\ncount still_incoming={len(still_incoming)}")

    print("\n=== ledger complete, only under processed/ (poll stale or not moved at source) ===")
    for line in only_processed:
        print(line)
    print(f"count only_processed={len(only_processed)}")

    print("\n=== ledger complete, no objects at either path ===")
    for line in neither:
        print(line)
    print(f"count neither={len(neither)}")

    print("\n=== poll row not ledger-complete (skipped S3 move check) ===")
    for line in not_complete[:15]:
        print(line)
    if len(not_complete) > 15:
        print(f"... +{len(not_complete) - 15} more")
    print(f"count not_complete={len(not_complete)}")

    conn.close()


if __name__ == "__main__":
    main()
