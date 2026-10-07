#!/usr/bin/env python3
"""Ingest a portable submission zip: safe unpack → ClamAV → E-ARK DIP/AIP (via ingest-game-folder)."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import pyclamd
    import yaml
except ImportError as e:
    print(f"Missing dependency: {e}", file=sys.stderr)
    sys.exit(1)

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
from submission_common import (  # noqa: E402
    SubmissionError,
    SubmissionManifest,
    build_game_config,
    extract_zip_safely,
    inspect_zip,
    limits_from_dict,
    load_manifest,
    resolve_source_folder,
    security_from_dict,
    slug_game_key,
    validate_allowlist,
)
from normalize_display_title import normalize_display_title  # noqa: E402


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


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


def scan_file(clamd: pyclamd.ClamdNetworkSocket, path: Path) -> None:
    result = clamd.scan_file(str(path))
    if result is None:
        return
    for _scan, status in result.items():
        if status[0] == "FOUND":
            raise RuntimeError(f"Virus detected in {path}: {status[1]}")


def submission_paths(sub_cfg: dict, ingest_paths: dict) -> dict[str, Path]:
    root = Path(sub_cfg["paths"]["root"])
    return {
        "root": root,
        "incoming": root / sub_cfg["paths"]["incoming"],
        "workspace": root / sub_cfg["paths"]["workspace"],
        "processed": root / sub_cfg["paths"]["processed"],
        "failed": root / sub_cfg["paths"]["failed"],
        "game_config_dir": Path(sub_cfg["paths"]["game_config_dir"]),
        "output_root": Path(ingest_paths["output_root"]),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "zip_path",
        type=Path,
        help="Path to submission .zip inside the container (e.g. /data/submissions/incoming/pkg.zip)",
    )
    parser.add_argument("--game-key", help="Override game_key (must be unique in ledger)")
    parser.add_argument("--title", help="Override Omeka item title")
    parser.add_argument(
        "--submissions-config",
        type=Path,
        default=Path("/config/preservation/submissions.yaml"),
    )
    parser.add_argument(
        "--ingest-config",
        type=Path,
        default=Path("/config/preservation/ingest.yaml"),
    )
    parser.add_argument(
        "--skip-ingest",
        action="store_true",
        help="Unpack and write game config only (no ingest-game-folder)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    zip_path = args.zip_path.resolve()
    if not zip_path.is_file():
        raise SystemExit(f"Zip not found: {zip_path}")

    sub_cfg = load_yaml(args.submissions_config)
    ingest_cfg = load_yaml(args.ingest_config)
    paths = submission_paths(sub_cfg, ingest_cfg["paths"])
    limits = limits_from_dict(sub_cfg.get("limits") or {})
    security = security_from_dict(sub_cfg.get("security") or {})

    for key in ("incoming", "workspace", "processed", "failed", "game_config_dir"):
        paths[key].mkdir(parents=True, exist_ok=True)

    clam = ingest_cfg["clamav"]
    clamd = wait_for_clamd(clam["host"], int(clam["port"]), int(clam.get("connect_timeout_seconds", 120)))

    workspace = paths["workspace"] / zip_path.stem
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)

    try:
        inspect_zip(zip_path, limits, security)
        scan_file(clamd, zip_path)
        written = extract_zip_safely(zip_path, workspace / "extracted", limits, security)
        print(f"Extracted {written} bytes")

        extract_root = workspace / "extracted"
        source_folder = resolve_source_folder(extract_root)
        validate_allowlist(source_folder, security.allowed_extensions)
        manifest_names = list(sub_cfg.get("manifest_filenames") or [])
        manifest = load_manifest(
            extract_root, manifest_names, max_manifest_bytes=limits.max_manifest_bytes
        )
        if not manifest.game_key and not manifest.omeka_item_title:
            inner = load_manifest(
                source_folder,
                manifest_names,
                max_manifest_bytes=limits.max_manifest_bytes,
            )
            manifest = SubmissionManifest(
                game_key=inner.game_key or manifest.game_key,
                omeka_item_title=inner.omeka_item_title or manifest.omeka_item_title,
                material_type=inner.material_type or manifest.material_type,
                submitter=inner.submitter or manifest.submitter,
                notes=inner.notes or manifest.notes,
            )

        game_key = args.game_key or manifest.game_key or slug_game_key(zip_path.stem)
        title_raw = args.title or manifest.omeka_item_title or zip_path.stem.replace("_", " ")
        omeka_title = normalize_display_title(title_raw)

        pres = dict(ingest_cfg.get("preservation") or {})
        pres.setdefault("agent_version", "ingest-submission.py")
        batch_key = str((sub_cfg.get("preservation") or {}).get("batch_key") or "portable-submissions")
        pres_block = sub_cfg.get("preservation") or {}
        max_files = int(pres_block.get("max_files", 5000))
        max_total_bytes = int(pres_block.get("max_total_bytes", 5368709120))
        video_access = pres_block.get("video_access")
        if isinstance(video_access, dict):
            video_access = dict(video_access)
        else:
            video_access = None

        game_cfg = build_game_config(
            source_game_folder=source_folder,
            game_key=game_key,
            omeka_item_title=omeka_title,
            output_root=paths["output_root"],
            batch_key=batch_key,
            max_files=max_files,
            max_total_bytes=max_total_bytes,
            preservation=pres,
            video_access=video_access,
        )
        if manifest.material_type or manifest.submitter or manifest.notes:
            game_cfg["submission"].update(
                {
                    k: v
                    for k, v in {
                        "material_type": manifest.material_type,
                        "submitter": manifest.submitter,
                        "notes": manifest.notes,
                        "source_zip": str(zip_path),
                    }.items()
                    if v
                }
            )

        game_cfg_path = paths["game_config_dir"] / f"{game_key}.yaml"
        game_cfg_path.write_text(yaml.safe_dump(game_cfg, sort_keys=False), encoding="utf-8")
        print(f"Wrote game config: {game_cfg_path}")
        print(f"  source_game_folder: {source_folder}")
        print(f"  game_key: {game_key}")

        if args.skip_ingest:
            dest = paths["processed"] / zip_path.name
            shutil.move(str(zip_path), dest)
            print(f"Zip moved to {dest} (--skip-ingest)")
            return

        subprocess.run(
            [sys.executable, str(SCRIPTS / "ingest-game-folder.py"), str(game_cfg_path)],
            check=True,
        )
        dest = paths["processed"] / zip_path.name
        if zip_path.is_file():
            shutil.move(str(zip_path), dest)
        print(f"Submission complete; zip archived to {dest}")
    except (SubmissionError, RuntimeError, subprocess.CalledProcessError) as e:
        failed_dir = paths["failed"]
        failed_dir.mkdir(parents=True, exist_ok=True)
        if zip_path.is_file():
            shutil.move(str(zip_path), failed_dir / zip_path.name)
        (workspace / "error.txt").write_text(f"{e}\n", encoding="utf-8")
        raise SystemExit(str(e)) from e


if __name__ == "__main__":
    main()
