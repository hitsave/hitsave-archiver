#!/usr/bin/env python3
"""Verify ledger-complete dump packages exist on Wasabi (BagIt layout + disc payload)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    import psycopg
    from botocore.exceptions import ClientError
except ImportError as e:
    print(f"Missing dependency: {e}", file=sys.stderr)
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from preservation_common import (  # noqa: E402
    load_ingest_config,
    resolve_wasabi_bucket,
    wasabi_unpacked_prefix,
)
from upload_bag_wasabi import load_wasabi_settings, make_s3_client  # noqa: E402
from yaml_config import load_yaml  # noqa: E402


def pg_connect(db_cfg: dict):
    return psycopg.connect(
        host=db_cfg["host"],
        port=int(db_cfg.get("port", 5432)),
        dbname=db_cfg["database"],
        user=db_cfg["user"],
        password=db_cfg["password"],
    )


def ready_slug_from_source(source_path: str) -> str:
    return source_path.rstrip("/").split("/")[-1]


def list_s3_keys(client, bucket: str, prefix: str) -> list[str]:
    root = wasabi_unpacked_prefix(prefix)
    keys: list[str] = []
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": root}
        if token:
            kwargs["ContinuationToken"] = token
        resp = client.list_objects_v2(**kwargs)
        for obj in resp.get("Contents") or []:
            keys.append(str(obj["Key"]))
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")
    return keys


def validate_keys(keys: list[str], *, prefix: str) -> tuple[bool, str]:
    root = wasabi_unpacked_prefix(prefix)
    if not keys:
        return False, "no objects under prefix"
    rels = [k[len(root) :] if k.startswith(root) else k for k in keys]
    has_bagit = any(r == "bagit.txt" for r in rels)
    has_manifest = any(r == "manifest-sha256.txt" for r in rels)
    has_iso = any(".iso" in r.lower() for r in rels)
    if not has_bagit:
        return False, "missing bagit.txt"
    if not has_manifest:
        return False, "missing manifest-sha256.txt"
    if not has_iso:
        return False, "missing .iso under prefix"
    return True, f"{len(keys)} objects, bagit+manifest+iso present"


def main() -> None:
    report_path: Path | None = None
    argv = sys.argv[1:]
    if "--report" in argv:
        idx = argv.index("--report")
        report_path = Path(argv[idx + 1])
        argv = argv[:idx] + argv[idx + 2 :]

    ingest_path = Path("/config/preservation/ingest.yaml")
    ingest = load_ingest_config()
    db_cfg = load_yaml(Path(ingest["database"]["config_file"]))["postgres"]
    wasabi, _ = load_wasabi_settings(ingest_path)
    from upload_bag_wasabi import resolve_config_path  # noqa: E402

    creds = load_yaml(resolve_config_path(str(wasabi["credentials_file"]), ingest_path.parent.parent))
    client = make_s3_client(wasabi, creds)

    with pg_connect(db_cfg) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT game_key, source_path, package_storage_prefix, aip_wasabi_prefix,
                       total_bytes, file_count
                FROM game_ingest
                WHERE ingest_kind = 'dump' AND status = 'complete'
                ORDER BY game_key
                """
            )
            rows = cur.fetchall()

    results: list[dict] = []
    ok_count = 0
    for game_key, source_path, storage_prefix, aip_uri, total_bytes, file_count in rows:
        slug = ready_slug_from_source(str(source_path or ""))
        prefix = str(storage_prefix or "").strip("/")
        entry: dict = {
            "game_key": game_key,
            "ready_slug": slug,
            "package_storage_prefix": prefix,
            "aip_wasabi_prefix": aip_uri,
            "ok": False,
            "detail": "",
        }
        if not prefix:
            entry["detail"] = "missing package_storage_prefix"
            results.append(entry)
            print(f"FAIL {slug}: {entry['detail']}")
            continue
        try:
            bucket = resolve_wasabi_bucket(wasabi, "dump")
            keys = list_s3_keys(client, bucket, prefix)
            ok, detail = validate_keys(keys, prefix=prefix)
            entry["ok"] = ok
            entry["detail"] = detail
            entry["s3_object_count"] = len(keys)
            if ok:
                ok_count += 1
                print(f"OK   {slug}: {detail}")
            else:
                print(f"FAIL {slug}: {detail}")
        except ClientError as e:
            entry["detail"] = f"S3 error: {e}"
            print(f"FAIL {slug}: {entry['detail']}")
        results.append(entry)

    summary = {
        "total": len(rows),
        "ok": ok_count,
        "failed": len(rows) - ok_count,
        "packages": results,
    }
    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(f"Report: {report_path}")

    print(f"Wasabi validation: {ok_count}/{len(rows)} ok")
    if ok_count != len(rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
