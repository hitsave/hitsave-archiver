#!/usr/bin/env python3
"""Rebuild E-ARK AIP/DIP packages from game configs and refresh Omeka DIP media."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = Path(os.environ.get("HITSAVE_CONFIG_ROOT", "/config"))
if not CONFIG.is_dir():
    CONFIG = ROOT / "config"
OUTPUT_DIP_ROOT = Path("/tank/hitsave-archiver/output/dip")


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def config_in_container(cfg_path: Path) -> str:
    try:
        rel = cfg_path.relative_to(ROOT / "config")
        return f"/config/{rel.as_posix()}"
    except ValueError:
        return str(cfg_path)


def resolve_game_config(game_key: str) -> Path:
    single = CONFIG / "preservation/game.yml"
    if single.is_file():
        data = load_yaml(single)
        if str(data.get("game_key") or "") == game_key:
            return single
    matches = sorted((CONFIG / "preservation/generated").rglob(f"{game_key}.yaml"))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise SystemExit(f"Ambiguous configs for {game_key}: {matches}")
    raise SystemExit(f"No config found for game_key={game_key}")


def dip_host_path(output_tar: str) -> Path:
    rel = output_tar.removeprefix("/output/dip/").lstrip("/")
    return OUTPUT_DIP_ROOT / rel


def dip_path_in_omeka_container(output_tar: str) -> str:
    rel = output_tar.removeprefix("/output/dip/").lstrip("/")
    return f"/dip-output/{rel}"


def psql_rows(query: str) -> list[tuple[str, str, str]]:
    cmd = [
        "docker",
        "compose",
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "hitsave",
        "-d",
        "hitsave_ledger",
        "-tAc",
        query,
    ]
    out = subprocess.check_output(cmd, cwd=ROOT, text=True).strip()
    if not out:
        return []
    rows: list[tuple[str, str, str]] = []
    for line in out.splitlines():
        parts = line.split("|")
        if len(parts) >= 3:
            rows.append((parts[0], parts[1], parts[2]))
    return rows


def run_build(game_key: str, cfg_path: Path, *, skip_wasabi: bool) -> None:
    cfg = load_yaml(cfg_path)
    cfg_arg = config_in_container(cfg_path)

    print(f"=== {game_key}: E-ARK DIP ===")
    subprocess.run(
        [
            "docker",
            "compose",
            "run",
            "--rm",
            "--entrypoint",
            "python",
            "ingest-worker",
            "/app/scripts/build-dip-e-ark.py",
            cfg_arg,
        ],
        cwd=ROOT,
        check=True,
    )
    print(f"=== {game_key}: E-ARK AIP ===")
    subprocess.run(
        [
            "docker",
            "compose",
            "run",
            "--rm",
            "--entrypoint",
            "python",
            "ingest-worker",
            "/app/scripts/build-aip-e-ark.py",
            cfg_arg,
        ],
        cwd=ROOT,
        check=True,
    )

    ingest = load_yaml(CONFIG / "preservation/ingest.yaml")
    wasabi = ingest.get("wasabi") or {}
    if wasabi.get("enabled") and not skip_wasabi:
        print(f"=== {game_key}: Wasabi AIP upload ===")
        subprocess.run(
            [
                "docker",
                "compose",
                "run",
                "--rm",
                "--entrypoint",
                "python",
                "ingest-worker",
                "/app/scripts/upload-aip-wasabi.py",
                cfg["aip_bag_dir"],
                game_key,
            ],
            cwd=ROOT,
            check=True,
        )


def replace_omeka_dip(media_id: int, dip_container_path: str) -> None:
    subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "omeka",
            "php",
            "/scripts/replace-dip-media-original.php",
            str(media_id),
            dip_container_path,
        ],
        cwd=ROOT / "omeka-test",
        check=True,
    )


def upload_omeka_new(game_key: str, cfg_path: Path) -> None:
    subprocess.run(
        [
            "docker",
            "compose",
            "run",
            "--rm",
            "omeka-uploader",
            game_key,
            config_in_container(cfg_path),
        ],
        cwd=ROOT,
        check=True,
    )


def verify_dip_index(dip_container_path: str) -> int:
    out = subprocess.check_output(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "-e",
            "DIP_VIEWER_PARSE_JSON_ONLY=1",
            "omeka",
            "php",
            "/var/www/html/volume/modules/OmekaDipViewer/test/parse_fixture.php",
            dip_container_path,
        ],
        cwd=ROOT / "omeka-test",
        text=True,
    )
    decoder = json.JSONDecoder()
    data, _ = decoder.raw_decode(out.lstrip())
    return len(data.get("files") or [])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-key", action="append", dest="game_keys")
    parser.add_argument("--skip-wasabi", action="store_true")
    parser.add_argument("--skip-omeka", action="store_true")
    parser.add_argument("--build-only", action="store_true")
    args = parser.parse_args()

    if args.game_keys:
        keys_sql = ",".join(f"'{k.replace(chr(39), chr(39)+chr(39))}'" for k in args.game_keys)
        targets = psql_rows(
            "SELECT game_key, COALESCE(omeka_item_id::text,''), COALESCE(omeka_media_id::text,'') "
            f"FROM game_ingest WHERE game_key IN ({keys_sql}) ORDER BY game_key"
        )
        found = {t[0] for t in targets}
        for k in args.game_keys:
            if k not in found:
                targets.append((k, "", ""))
    else:
        targets = psql_rows(
            "SELECT game_key, COALESCE(omeka_item_id::text,''), COALESCE(omeka_media_id::text,'') "
            "FROM game_ingest WHERE status='complete' ORDER BY game_key"
        )

    for game_key, _item_id, media_id in targets:
        cfg_path = resolve_game_config(game_key)
        run_build(game_key, cfg_path, skip_wasabi=args.skip_wasabi)

        if args.build_only or args.skip_omeka:
            continue

        cfg = load_yaml(cfg_path)
        host_dip = dip_host_path(cfg["output_tar"])
        if not host_dip.is_file():
            raise SystemExit(f"Missing DIP on host: {host_dip}")
        dip_in_omeka = dip_path_in_omeka_container(cfg["output_tar"])

        if media_id.isdigit():
            print(f"=== {game_key}: replace Omeka media {media_id} ===")
            replace_omeka_dip(int(media_id), dip_in_omeka)
        else:
            print(f"=== {game_key}: Omeka upload (new item) ===")
            upload_omeka_new(game_key, cfg_path)

        count = verify_dip_index(dip_in_omeka)
        print(f"  indexed {count} DIP files")
        if count < 1:
            raise SystemExit(f"DIP index empty for {game_key}")

    print("Rebuild complete.")


if __name__ == "__main__":
    main()
