#!/usr/bin/env python3
"""Upload unpacked BagIt for press_material/other kinds (wasabi_root from ingest-kinds.yaml)."""
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

from package_identity import relative_sip_path  # noqa: E402
from preservation_common import (  # noqa: E402
    material_root_for_kind,
    resolve_wasabi_bucket,
    wasabi_package_storage_prefix,
)
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


def _ledger_upload_context(game_key: str, ingest_config_path: Path) -> tuple[str, str, str, str]:
    ingest = load_yaml(ingest_config_path)
    db_path = Path(ingest["database"]["config_file"])
    db_cfg = load_yaml(db_path)["postgres"]
    with pg_connect(db_cfg) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT package_id, sort_letter, ingest_kind, source_path, package_storage_prefix
                FROM game_ingest WHERE game_key = %s
                """,
                (game_key,),
            )
            row = cur.fetchone()
    if not row:
        raise RuntimeError(f"No game_ingest row for game_key={game_key!r}")
    package_id = str(row[0] or game_key)
    sort_letter = str(row[1] or "X")
    ingest_kind = str(row[2] or "").strip()
    source_path = str(row[3] or "").strip()
    existing_prefix = str(row[4] or "").strip()
    if not ingest_kind:
        raise RuntimeError(f"game_ingest.ingest_kind is required for Wasabi upload: {game_key!r}")
    if ingest_kind == "dump":
        raise RuntimeError("ingest kind dump must use upload-dump-wasabi.py, not press upload")
    if existing_prefix:
        storage_prefix = existing_prefix.strip("/")
    else:
        if not source_path:
            raise RuntimeError(f"game_ingest.source_path required to build Wasabi prefix: {game_key!r}")
        rel = relative_sip_path(
            Path(source_path),
            material_root_for_kind(ingest_kind, ingest.get("paths") or {}),
        )
        storage_prefix = wasabi_package_storage_prefix(
            ingest_kind,
            package_id=package_id,
            sort_letter=sort_letter,
            relative_sip_path=rel,
        )
    return package_id, sort_letter, ingest_kind, storage_prefix


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
    package_id, sort_letter, ingest_kind, storage_prefix = _ledger_upload_context(
        game_key, ingest_path
    )
    target_bucket = resolve_wasabi_bucket(wasabi, ingest_kind)

    try:
        upload = upload_bag_to_prefix(
            bag_dir,
            storage_prefix,
            ingest_config_path=ingest_path,
            bucket=target_bucket,
        )
        update_ledger_dump_storage(game_key, upload, ingest_config_path=ingest_path)
    except ClientError as e:
        raise SystemExit(f"Wasabi S3 error: {e}") from e


if __name__ == "__main__":
    main()
