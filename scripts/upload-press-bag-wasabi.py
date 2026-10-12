#!/usr/bin/env python3
"""Upload an unpacked BagIt press package to Wasabi under press-material/{letter}/{package_id}/."""
from __future__ import annotations

import sys
from pathlib import Path

try:
    from botocore.exceptions import ClientError
except ImportError as e:
    print(f"Missing dependency: {e}", file=sys.stderr)
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INGEST_CONFIG = Path("/config/preservation/ingest.yaml")
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from preservation_common import wasabi_bagit_unpacked_prefix  # noqa: E402
import importlib.util

_dump_spec = importlib.util.spec_from_file_location(
    "upload_dump_wasabi", REPO_ROOT / "scripts" / "upload-dump-wasabi.py"
)
assert _dump_spec and _dump_spec.loader
_upload_dump = importlib.util.module_from_spec(_dump_spec)
_dump_spec.loader.exec_module(_upload_dump)
upload_bag_to_prefix = _upload_dump.upload_bag_to_prefix
update_ledger_dump_storage = _upload_dump.update_ledger_dump_storage
load_yaml = _upload_dump.load_yaml
pg_connect = _upload_dump.pg_connect


def _ledger_package_fields(game_key: str, ingest_config_path: Path) -> tuple[str, str]:
    ingest = load_yaml(ingest_config_path)
    db_path = Path(ingest["database"]["config_file"])
    db_cfg = load_yaml(db_path)["postgres"]
    with pg_connect(db_cfg) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT package_id, sort_letter FROM game_ingest WHERE game_key = %s
                """,
                (game_key,),
            )
            row = cur.fetchone()
    if not row:
        raise RuntimeError(f"No game_ingest row for game_key={game_key!r}")
    package_id = str(row[0] or game_key)
    sort_letter = str(row[1] or "X")
    return package_id, sort_letter


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit(
            "Usage: upload-press-bag-wasabi.py <bag_dir> <game_key> [ingest.yaml]"
        )
    bag_dir = Path(sys.argv[1])
    game_key = sys.argv[2]
    ingest_path = Path(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_INGEST_CONFIG

    ingest = load_yaml(ingest_path)
    wasabi = ingest.get("wasabi") or {}
    prefix_root = str(wasabi.get("prefix") or "press-material")
    package_id, sort_letter = _ledger_package_fields(game_key, ingest_path)
    storage_prefix = wasabi_bagit_unpacked_prefix(
        prefix_root,
        package_id=package_id,
        sort_letter=sort_letter,
    )

    try:
        upload = upload_bag_to_prefix(bag_dir, storage_prefix, ingest_config_path=ingest_path)
        update_ledger_dump_storage(game_key, upload, ingest_config_path=ingest_path)
    except ClientError as e:
        raise SystemExit(f"Wasabi S3 error: {e}") from e


if __name__ == "__main__":
    main()
