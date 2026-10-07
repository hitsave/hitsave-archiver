#!/usr/bin/env python3
"""Ingest one game folder: ClamAV → E-ARK DIP tar → E-ARK AIP → ledger row."""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
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
from preservation_common import resolve_game_config  # noqa: E402


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


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


def scan_tree(clamd: pyclamd.ClamdNetworkSocket, root: Path) -> None:
    # SCAN uses paths visible to the clamd container (same /data/press-material mount).
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        result = clamd.scan_file(str(path))
        if result is None:
            continue
        for _scan, status in result.items():
            if status[0] == "FOUND":
                raise RuntimeError(f"Virus detected in {path}: {status[1]}")


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
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO game_ingest (
                game_key, source_path, status, dip_path, aip_bag_path, package_uuid,
                file_count, total_bytes, checksum_manifest_path, error_message, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW()
            )
            ON CONFLICT (game_key) DO UPDATE SET
                source_path = EXCLUDED.source_path,
                status = EXCLUDED.status,
                dip_path = EXCLUDED.dip_path,
                aip_bag_path = EXCLUDED.aip_bag_path,
                package_uuid = EXCLUDED.package_uuid,
                file_count = EXCLUDED.file_count,
                total_bytes = EXCLUDED.total_bytes,
                checksum_manifest_path = EXCLUDED.checksum_manifest_path,
                error_message = EXCLUDED.error_message,
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
            ),
        )
    conn.commit()


def main() -> None:
    ingest_cfg_path = Path("/config/preservation/ingest.yaml")
    game_cfg_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/config/preservation/game.yml")

    if not ingest_cfg_path.is_file():
        raise SystemExit(f"Missing {ingest_cfg_path}")
    if not game_cfg_path.is_file():
        raise SystemExit(f"Missing game config: {game_cfg_path}")

    ingest_cfg = load_yaml(ingest_cfg_path)
    game_cfg = resolve_game_config(load_yaml(game_cfg_path), agent_version="ingest-game-folder.py")

    db_path = Path(ingest_cfg["database"]["config_file"])
    db_cfg = load_yaml(db_path)["postgres"]

    paths = ingest_cfg["paths"]
    output_root = Path(paths["output_root"])
    ensure_dirs(
        output_root / paths["dip_subdir"],
        output_root / paths["aip_subdir"],
        output_root / paths["processed_subdir"],
        output_root / paths["quarantine_subdir"],
    )

    source = Path(game_cfg["source_game_folder"]).resolve()
    game_key = game_cfg.get("game_key") or source.name
    dip_path = Path(game_cfg["output_tar"]).resolve()
    aip_bag = Path(game_cfg["aip_bag_dir"]).resolve()

    clam = ingest_cfg["clamav"]
    clamd = wait_for_clamd(clam["host"], int(clam["port"]), int(clam.get("connect_timeout_seconds", 120)))

    conn = pg_connect(db_cfg)
    try:
        upsert_ledger(conn, game_key=game_key, source_path=str(source), status="scanning")
        scan_tree(clamd, source)

        upsert_ledger(conn, game_key=game_key, source_path=str(source), status="building_dip")
        subprocess.run(
            [sys.executable, str(SCRIPTS / "build-dip-e-ark.py"), str(game_cfg_path)],
            check=True,
        )

        upsert_ledger(conn, game_key=game_key, source_path=str(source), status="building_aip")
        subprocess.run(
            [sys.executable, str(SCRIPTS / "build-aip-e-ark.py"), str(game_cfg_path)],
            check=True,
        )

        wasabi_cfg = ingest_cfg.get("wasabi") or {}
        if wasabi_cfg.get("enabled", False):
            upsert_ledger(conn, game_key=game_key, source_path=str(source), status="uploading_aip")
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "upload-aip-wasabi.py"),
                    str(aip_bag),
                    game_key,
                    str(ingest_cfg_path),
                ],
                check=True,
            )
        else:
            print("Wasabi upload skipped (wasabi.enabled is false or section missing)")

        manifest_path = aip_bag / "metadata" / "other" / "checksums-sha256.txt"
        package_uuid = None
        root_mets = aip_bag / "METS.xml"
        if root_mets.is_file():
            import re

            text = root_mets.read_text(encoding="utf-8", errors="replace")
            match = re.search(r'\bOBJID="([^"]+)"', text)
            if match:
                package_uuid = match.group(1)

        moby = subprocess.run(
            [sys.executable, str(SCRIPTS / "moby_enrich_game.py"), str(game_cfg_path)],
            check=False,
        )
        if moby.returncode == 2:
            raise RuntimeError("MobyGames metadata blocked this game (see ledger block_reason)")

        upsert_ledger(
            conn,
            game_key=game_key,
            source_path=str(source),
            status="complete",
            dip_path=str(dip_path),
            aip_bag_path=str(aip_bag),
            package_uuid=package_uuid,
            checksum_manifest_path=str(manifest_path) if manifest_path.is_file() else None,
            error_message=None,
        )
        print(f"Ingest complete: {game_key}")
        print(f"  DIP: {dip_path}")
        print(f"  AIP: {aip_bag}")
    except Exception as e:
        upsert_ledger(
            conn,
            game_key=game_key,
            source_path=str(source),
            status="failed",
            error_message=str(e),
        )
        quarantine = output_root / paths["quarantine_subdir"] / game_key
        quarantine.mkdir(parents=True, exist_ok=True)
        (quarantine / "error.txt").write_text(f"{datetime.now(timezone.utc).isoformat()}\n{e}\n", encoding="utf-8")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
