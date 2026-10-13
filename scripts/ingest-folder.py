#!/usr/bin/env python3
"""Ingest one SIP folder: ClamAV → BagIt → Wasabi → ledger."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

try:
    import psycopg
    import pyclamd
    import yaml
except ImportError as e:
    print(f"Missing dependency: {e}", file=sys.stderr)
    sys.exit(1)

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
from package_ledger import resolve_package_identity  # noqa: E402
from dump_classify import classify_dump_sip  # noqa: E402
from ingest_kind import moby_enrich_enabled, resolve_ingest_kind  # noqa: E402
from preservation_common import (  # noqa: E402
    material_root_for_kind,
    resolve_game_config,
    wasabi_package_storage_prefix,
)
from premis_virus_check import VirusScanReport, attach_virus_check_premis, scan_sip_tree  # noqa: E402
from source_content_policy import (  # noqa: E402
    ContentPolicyError,
    check_source_content_policy,
    write_quarantine_details,
)


from yaml_config import load_yaml  # noqa: E402


def pg_connect(db_cfg: dict):
    return psycopg.connect(
        host=db_cfg["host"],
        port=int(db_cfg.get("port", 5432)),
        dbname=db_cfg["database"],
        user=db_cfg["user"],
        password=db_cfg["password"],
    )


def wait_for_clamd(host: str, port: int, timeout: int) -> pyclamd.ClamdNetworkSocket:
    import time

    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            cd = pyclamd.ClamdNetworkSocket(host=host, port=port, timeout=30)
            cd.ping()
            return cd
        except Exception as e:
            last_error = e
            time.sleep(3)
    raise RuntimeError(f"ClamAV not ready at {host}:{port}: {last_error}")


def ensure_dirs(*paths: Path) -> None:
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)


def upsert_ledger(
    conn,
    *,
    game_key: str,
    source_path: str,
    status: str,
    dip_path: str | None = None,
    aip_bag_path: str | None = None,
    package_uuid: str | None = None,
    file_count: int | None = None,
    total_bytes: int | None = None,
    checksum_manifest_path: str | None = None,
    error_message: str | None = None,
    package_format: str | None = None,
    submission_source_id: str | None = None,
    inventory_json: dict | None = None,
    sip_fingerprint: str | None = None,
    package_id: str | None = None,
    title_slug: str | None = None,
    sort_letter: str | None = None,
    ingest_kind: str | None = None,
    storage_layout: str | None = None,
    package_storage_prefix: str | None = None,
    dump_class: str | None = None,
    dump_system: str | None = None,
    dump_category: str | None = None,
) -> None:
    inv_param = json.dumps(inventory_json) if inventory_json is not None else None
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO game_ingest (
                game_key, source_path, status, dip_path, aip_bag_path, package_uuid,
                file_count, total_bytes, checksum_manifest_path, error_message,
                package_format, submission_source_id, inventory_json,
                sip_fingerprint, package_id, title_slug, sort_letter, ingest_kind,
                storage_layout, package_storage_prefix, dump_class, dump_system, dump_category,
                updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW()
            )
            ON CONFLICT (game_key) DO UPDATE SET
                source_path = EXCLUDED.source_path,
                status = EXCLUDED.status,
                dip_path = EXCLUDED.dip_path,
                aip_bag_path = EXCLUDED.aip_bag_path,
                package_uuid = COALESCE(EXCLUDED.package_uuid, game_ingest.package_uuid),
                file_count = EXCLUDED.file_count,
                total_bytes = EXCLUDED.total_bytes,
                checksum_manifest_path = EXCLUDED.checksum_manifest_path,
                error_message = EXCLUDED.error_message,
                package_format = COALESCE(EXCLUDED.package_format, game_ingest.package_format),
                submission_source_id = COALESCE(EXCLUDED.submission_source_id, game_ingest.submission_source_id),
                inventory_json = COALESCE(EXCLUDED.inventory_json, game_ingest.inventory_json),
                sip_fingerprint = COALESCE(EXCLUDED.sip_fingerprint, game_ingest.sip_fingerprint),
                package_id = COALESCE(EXCLUDED.package_id, game_ingest.package_id),
                title_slug = COALESCE(EXCLUDED.title_slug, game_ingest.title_slug),
                sort_letter = COALESCE(EXCLUDED.sort_letter, game_ingest.sort_letter),
                ingest_kind = COALESCE(EXCLUDED.ingest_kind, game_ingest.ingest_kind),
                storage_layout = COALESCE(EXCLUDED.storage_layout, game_ingest.storage_layout),
                package_storage_prefix = COALESCE(
                    EXCLUDED.package_storage_prefix, game_ingest.package_storage_prefix
                ),
                dump_class = COALESCE(EXCLUDED.dump_class, game_ingest.dump_class),
                dump_system = COALESCE(EXCLUDED.dump_system, game_ingest.dump_system),
                dump_category = COALESCE(EXCLUDED.dump_category, game_ingest.dump_category),
                updated_at = NOW()
            """,
            (
                game_key,
                source_path,
                status,
                dip_path,
                aip_bag_path,
                package_uuid,
                file_count,
                total_bytes,
                checksum_manifest_path,
                error_message,
                package_format,
                submission_source_id,
                inv_param,
                sip_fingerprint,
                package_id,
                title_slug,
                sort_letter,
                ingest_kind,
                storage_layout,
                package_storage_prefix,
                dump_class,
                dump_system,
                dump_category,
            ),
        )
    conn.commit()


def read_bagit_metadata(bag_dir: Path) -> tuple[dict | None, dict | None, str | None]:
    meta = bag_dir / "metadata"
    submission = None
    inventory = None
    sub_path = meta / "submission.json"
    inv_path = meta / "inventory.json"
    if sub_path.is_file():
        submission = json.loads(sub_path.read_text(encoding="utf-8"))
    if inv_path.is_file():
        inventory = json.loads(inv_path.read_text(encoding="utf-8"))
    manifest = bag_dir / "manifest-sha256.txt"
    checksum_path = str(manifest) if manifest.is_file() else None
    return submission, inventory, checksum_path


def run_bagit_ingest(
    conn,
    *,
    game_cfg_path: Path,
    game_cfg: dict,
    ingest_cfg_path: Path,
    ingest_cfg: dict,
    source: Path,
    game_key: str,
    virus_scan: VirusScanReport | None = None,
) -> tuple[Path, dict | None, dict | None, str | None, str | None]:
    bag_dir = Path(game_cfg["bagit_bag_dir"]).resolve()

    upsert_ledger(conn, game_key=game_key, source_path=str(source), status="building_bagit")
    subprocess.run(
        [sys.executable, str(SCRIPTS / "build-bagit-package.py"), str(game_cfg_path)],
        check=True,
    )

    if virus_scan is not None:
        from preservation_common import load_packaging_config  # noqa: E402

        pkg = load_packaging_config()
        bag_cfg = pkg.get("bagit") or {}
        meta_dir = str(bag_cfg.get("metadata_dir") or "metadata")
        premis_rel = str(bag_cfg.get("premis_virus_check_relpath") or "premis/virus-check-event.xml")
        premis_path = attach_virus_check_premis(
            bag_dir,
            virus_scan,
            metadata_dir=meta_dir,
            premis_relpath=premis_rel,
            linking_agent="ingest-folder.py",
        )
        print(f"  PREMIS virus check: {premis_path.relative_to(bag_dir)}")

    submission, inventory, checksum_path = read_bagit_metadata(bag_dir)
    submission_source_id = None
    package_uuid = None
    if submission:
        submission_source_id = submission.get("submission_source_id")
        package_uuid = submission.get("package_uuid")

    wasabi_cfg = ingest_cfg.get("wasabi") or {}
    kind = str(game_cfg.get("ingest_kind") or "")
    if wasabi_cfg.get("enabled", False):
        if kind == "dump":
            dump_clf = classify_dump_sip(source, package_id=game_key)
            upsert_ledger(
                conn,
                game_key=game_key,
                source_path=str(source),
                status="uploading_package",
                storage_layout="bagit_unpacked",
                package_storage_prefix=dump_clf.package_storage_prefix,
                dump_class=dump_clf.dump_class,
                dump_system=dump_clf.dump_system,
                dump_category=dump_clf.dump_category,
            )
        else:
            upsert_ledger(
                conn,
                game_key=game_key,
                source_path=str(source),
                status="uploading_package",
                storage_layout="bagit_unpacked",
            )
        subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "upload-bag-wasabi.py"),
                str(bag_dir),
                game_key,
                str(ingest_cfg_path),
            ],
            check=True,
        )
    else:
        print("Wasabi package upload skipped (wasabi.enabled is false or section missing)")

    return bag_dir, submission, inventory, checksum_path, submission_source_id, package_uuid


def main() -> None:
    ingest_cfg_path = Path("/config/preservation/ingest.yaml")
    game_cfg_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/config/preservation/game.yml")

    if not ingest_cfg_path.is_file():
        raise SystemExit(f"Missing {ingest_cfg_path}")
    if not game_cfg_path.is_file():
        raise SystemExit(f"Missing game config: {game_cfg_path}")

    ingest_cfg = load_yaml(ingest_cfg_path)
    raw_game_cfg = load_yaml(game_cfg_path)

    db_path = Path(ingest_cfg["database"]["config_file"])
    db_cfg = load_yaml(db_path)["postgres"]

    paths = ingest_cfg["paths"]
    output_root = Path(paths["output_root"])
    ensure_dirs(
        output_root / paths.get("bag_subdir", "bag"),
        output_root / paths["aip_subdir"],
        output_root / paths["processed_subdir"],
        output_root / paths["quarantine_subdir"],
    )

    if not raw_game_cfg.get("ingest_kind"):
        raise SystemExit("game config must set ingest_kind")
    kind = resolve_ingest_kind(raw_game_cfg)
    material_root = material_root_for_kind(kind, paths)
    if raw_game_cfg.get("source_subpath") and not raw_game_cfg.get("source_game_folder"):
        sub = str(raw_game_cfg["source_subpath"]).strip().strip("/")
        raw_game_cfg["source_game_folder"] = str(material_root / sub)
    source = Path(raw_game_cfg["source_game_folder"]).resolve()

    conn = pg_connect(db_cfg)
    identity = None
    try:
        catalog_title = str(raw_game_cfg.get("omeka_item_title") or source.name)
        identity = resolve_package_identity(
            conn,
            source=source,
            material_root=material_root,
            omeka_item_title=catalog_title,
        )
        game_key = identity.package_id
        package_uuid = identity.package_uuid
        raw_game_cfg["game_key"] = game_key
        dump_kwargs: dict = {}
        if kind == "dump":
            dump_clf = classify_dump_sip(source, package_id=identity.package_id)
            dump_kwargs = {
                "storage_layout": "bagit_unpacked",
                "package_storage_prefix": dump_clf.package_storage_prefix,
                "dump_class": dump_clf.dump_class,
                "dump_system": dump_clf.dump_system,
                "dump_category": dump_clf.dump_category,
            }
        else:
            dump_kwargs = {
                "storage_layout": "bagit_unpacked",
                "package_storage_prefix": wasabi_package_storage_prefix(
                    kind,
                    package_id=identity.package_id,
                    sort_letter=identity.sort_letter,
                    relative_sip_path=identity.relative_sip_path,
                ),
            }
        upsert_ledger(
            conn,
            game_key=game_key,
            source_path=str(source),
            status="registered",
            package_format="bagit",
            submission_source_id=identity.submission_source_id,
            sip_fingerprint=identity.sip_fingerprint,
            package_id=identity.package_id,
            title_slug=identity.title_slug,
            sort_letter=identity.sort_letter,
            package_uuid=package_uuid,
            ingest_kind=kind,
            **dump_kwargs,
        )
    except Exception:
        conn.close()
        raise

    game_cfg = resolve_game_config(raw_game_cfg, agent_version="ingest-folder.py")
    ingest_kind = game_cfg["ingest_kind"]

    clam = ingest_cfg["clamav"]
    clamd = wait_for_clamd(clam["host"], int(clam["port"]), int(clam.get("connect_timeout_seconds", 120)))

    quarantine_base = output_root / paths["quarantine_subdir"] / game_key
    try:
        check_source_content_policy(source, ingest_cfg)

        upsert_ledger(
            conn,
            game_key=game_key,
            source_path=str(source),
            status="scanning",
            ingest_kind=ingest_kind,
        )
        virus_scan = scan_sip_tree(clamd, source)

        bag_dir, _sub, inventory, checksum_manifest_path, submission_source_id, package_uuid = run_bagit_ingest(
            conn,
            game_cfg_path=game_cfg_path,
            game_cfg=game_cfg,
            ingest_cfg_path=ingest_cfg_path,
            ingest_cfg=ingest_cfg,
            source=source,
            game_key=game_key,
            virus_scan=virus_scan,
        )
        file_count: int | None = None
        total_bytes: int | None = None
        inventory_json: dict | None = inventory
        if inventory and inventory.get("summary"):
            file_count = int(inventory["summary"].get("file_count") or 0)
            total_bytes = int(inventory["summary"].get("total_bytes") or 0)

        if moby_enrich_enabled(game_cfg):
            moby = subprocess.run(
                [sys.executable, str(SCRIPTS / "moby_enrich_game.py"), str(game_cfg_path)],
                check=False,
            )
            if moby.returncode == 2:
                raise RuntimeError("MobyGames metadata blocked this game (see ledger block_reason)")

        complete_kwargs: dict = {
            "game_key": game_key,
            "source_path": str(source),
            "status": "complete",
            "ingest_kind": ingest_kind,
            "dip_path": str(bag_dir),
            "aip_bag_path": str(bag_dir),
            "package_uuid": package_uuid,
            "file_count": file_count,
            "total_bytes": total_bytes,
            "checksum_manifest_path": checksum_manifest_path,
            "error_message": None,
            "package_format": "bagit",
            "submission_source_id": submission_source_id,
            "inventory_json": inventory_json,
            "sip_fingerprint": identity.sip_fingerprint,
            "package_id": identity.package_id,
            "title_slug": identity.title_slug,
            "sort_letter": identity.sort_letter,
        }
        upsert_ledger(conn, **complete_kwargs)
        print(f"Ingest complete: {game_key} (bagit)")
        print(f"  package_id: {identity.package_id}")
        print(f"  sort_letter: {identity.sort_letter}")
        print(f"  sip_fingerprint: {identity.sip_fingerprint[:16]}…")
        if ingest_kind == "dump":
            dump_clf = classify_dump_sip(source, package_id=identity.package_id)
            print(f"  dump_class: {dump_clf.dump_class}")
            print(f"  wasabi prefix: {dump_clf.package_storage_prefix}/")
        print(f"  Bag dir: {bag_dir}")
    except ContentPolicyError as e:
        upsert_ledger(
            conn,
            game_key=game_key,
            source_path=str(source),
            status="quarantine",
            error_message=str(e),
        )
        write_quarantine_details(quarantine_base, exc=e, matches=e.matches)
        raise
    except Exception as e:
        upsert_ledger(
            conn,
            game_key=game_key,
            source_path=str(source),
            status="failed",
            error_message=str(e),
        )
        write_quarantine_details(quarantine_base, exc=e)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
