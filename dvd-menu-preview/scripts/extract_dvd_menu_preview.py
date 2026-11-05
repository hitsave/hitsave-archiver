#!/usr/bin/env python3
"""DVD-Video menu stills: Redump title-key descramble, VLC fallback for libdvdcss autocrack.

Batch:   extract_dvd_menu_preview.py [config.yaml]              (samples / sample_list_file)
Intake:  extract_dvd_menu_preview.py [config.yaml] --package <ready/<slug>> --out <dir>
         One staged package: runs only when its !submissionInfo.txt says "System: DVD-Video" and it
         has exactly one .iso. Writes <dir>/result.json always and <dir>/menu_preview.jpg on success;
         scratch (extracted VOBs, candidate frames) stays in a temp dir. Exit 0 unless the arguments
         are wrong: a missing menu must never block an ingest.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml
from PIL import Image

from dvd_css import (
    css_protection_disabled,
    extract_vob_from_iso,
    find_submission_info,
    menu_vob_candidates,
    parse_title_keys,
    prepare_vob_for_ffmpeg,
)
from frame_score import FrameScore, parse_still_source, pick_best_frame, score_frame

DEFAULT_CFG = Path("/config/dvd-menu-preview.yaml")


@dataclass
class PackageResult:
    iso: str
    status: str
    method: str | None
    preview: str | None
    best_score: float | None
    below_min_accept: bool
    source_vob: str | None
    source_time_sec: int | None
    top_candidates: list[dict]
    error: str | None


def load_cfg(path: Path) -> dict:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return raw.get("dvd_menu_preview") or {}


def load_sample_paths(cfg: dict, intake: Path) -> list[Path]:
    if cfg.get("sample_list_file"):
        list_path = Path(cfg["sample_list_file"])
        lines = list_path.read_text(encoding="utf-8").splitlines()
        rels = [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]
    else:
        rels = cfg.get("samples") or []
    return [intake / rel for rel in rels]


def slug_from_iso(iso: Path) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", iso.stem).strip("-").lower()


def _vlc_env() -> dict[str, str]:
    import os

    env = os.environ.copy()
    env.setdefault("XDG_RUNTIME_DIR", "/tmp/runtime-preview")
    Path(env["XDG_RUNTIME_DIR"]).mkdir(parents=True, exist_ok=True)
    return env


def cfg_run_user() -> str | None:
    import os

    if os.geteuid() != 0:
        return None
    return "preview"


def _vlc_cmd(dvd_target: str, out_dir: Path, t: int | float) -> list[str]:
    return [
        "cvlc",
        "--intf",
        "dummy",
        "--vout",
        "dummy",
        "--no-audio",
        "--no-video-title-show",
        "--no-sub-autodetect-file",
        "--start-time",
        str(t),
        "--run-time",
        "0.35",
        "--play-and-exit",
        "--video-filter=scene",
        f"--scene-path={out_dir.as_posix()}/",
        "--scene-format=png",
        f"--scene-prefix=menu_t{int(t):02d}_",
        "--scene-ratio=9999",
        dvd_target,
    ]


def capture_vlc_stills(
    iso: Path,
    out_dir: Path,
    times_sec: list[int | float],
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    dvd_target = f"dvd:///{iso.resolve().as_posix()}"
    captured: list[Path] = []
    run_user = cfg_run_user()
    vlc_env = _vlc_env()
    for t in times_sec:
        snap = out_dir / f"menu_t{int(t):02d}.png"
        if snap.is_file():
            snap.unlink()
        cmd = _vlc_cmd(dvd_target, out_dir, t)
        if run_user:
            cmd = ["runuser", "-u", run_user, "--"] + cmd
        subprocess.run(cmd, check=False, env=vlc_env)
        matches = sorted(out_dir.glob(f"menu_t{int(t):02d}_*.png"))
        if matches:
            matches[-1].rename(snap)
            captured.append(snap)
    return [p for p in captured if p.is_file()]


def capture_ffmpeg_stills(
    vob: Path,
    out_dir: Path,
    times_sec: list[int | float],
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    captured: list[Path] = []
    for t in times_sec:
        snap = out_dir / f"menu_t{int(t):02d}.jpg"
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                str(t),
                "-i",
                str(vob),
                "-frames:v",
                "1",
                str(snap),
            ],
            check=False,
        )
        if snap.is_file() and snap.stat().st_size > 0:
            captured.append(snap)
    return captured


def capture_with_title_keys(
    iso: Path,
    cfg: dict,
    work: Path,
    times_sec: list[int | float],
) -> tuple[list[Path], str] | tuple[None, str]:
    sidecar = find_submission_info(iso, cfg)
    if not sidecar:
        return None, "no_submission_sidecar"
    text = sidecar.read_text(encoding="utf-8", errors="replace")
    keys, no_key = parse_title_keys(text)
    unenc = css_protection_disabled(text)
    candidates = menu_vob_candidates(keys, no_key, unencrypted_disc=unenc)
    if not candidates:
        return None, "no_title_keys"

    scratch = work / "scratch"
    all_stills: list[Path] = []
    for vob_name, title_key in candidates:
        if title_key is None:
            print(f"  extract {vob_name} (no CSS title key — plaintext)")
        else:
            print(f"  descramble {vob_name} with Redump title key")
        enc_vob = scratch / vob_name
        clear_vob = scratch / f"{vob_name}.clear.vob"
        try:
            extract_vob_from_iso(iso, vob_name, enc_vob)
            prepare_vob_for_ffmpeg(enc_vob, clear_vob, title_key)
        except OSError as exc:
            print(f"    skip {vob_name}: {exc}")
            continue
        subdir = work / "candidates" / vob_name.replace(".", "_")
        stills = capture_ffmpeg_stills(clear_vob, subdir, times_sec)
        all_stills.extend(stills)
    if not all_stills:
        return None, "descramble_or_extract_failed"
    return all_stills, "redump_title_key"


def rank_stills(stills: list[Path], cfg: dict) -> list[FrameScore]:
    scored = [score_frame(p, cfg) for p in stills]
    return sorted(scored, key=lambda s: s.score, reverse=True)




def process_one(iso: Path, cfg: dict) -> PackageResult:
    output_root = Path(cfg["output_root"])
    times = cfg.get("sample_times_sec") or [5, 10]
    min_accept = float(cfg.get("min_accept_score", 0))
    slug = slug_from_iso(iso)
    work = output_root / slug
    rel_iso = str(iso)

    print(f"=== {iso.name} ===")
    if not iso.is_file():
        print(f"  skip: missing {iso}")
        return PackageResult(
            iso=rel_iso,
            status="missing",
            method=None,
            preview=None,
            best_score=None,
            below_min_accept=False,
            source_vob=None,
            source_time_sec=None,
            top_candidates=[],
            error="iso not found",
        )

    method = "redump_title_key"
    stills, note = capture_with_title_keys(iso, cfg, work, times)
    if not stills:
        print(f"  trying VLC/libdvdcss on ISO ({note})")
        method = "vlc_libdvdcss"
        stills = capture_vlc_stills(iso, work / "candidates" / "vlc", times)
        if not stills:
            return PackageResult(
                iso=rel_iso,
                status="failed",
                method=method,
                preview=None,
                best_score=None,
                below_min_accept=False,
                source_vob=None,
                source_time_sec=None,
                top_candidates=[],
                error=note,
            )

    ranked = rank_stills(stills, cfg)
    top = ranked[:5]
    top_payload = [
        {
            "file": str(s.path.relative_to(work)),
            "score": round(s.score, 2),
            "mean_luma": round(s.mean_luma, 1),
            "laplacian_variance": round(s.laplacian_variance, 1),
            "rejected": s.rejected,
            "reject_reason": s.reject_reason,
            **dict(
                zip(
                    ("source_vob", "source_time_sec"),
                    parse_still_source(s.path),
                    strict=True,
                )
            ),
        }
        for s in top
    ]

    best = pick_best_frame(ranked, cfg)
    if best is None:
        print("  no usable menu frame (all black or corrupt)")
        return PackageResult(
            iso=rel_iso,
            status="no_usable_frame",
            method=method,
            preview=None,
            best_score=None,
            below_min_accept=False,
            source_vob=None,
            source_time_sec=None,
            top_candidates=top_payload,
            error="all candidates rejected",
        )

    vob, t_sec = parse_still_source(best.path)
    below = best.score < min_accept
    dest = work / "menu_preview.jpg"
    with Image.open(best.path) as im:
        im.convert("RGB").save(dest, format="JPEG", quality=88, optimize=True)
    flag = " LOW_SCORE" if below else ""
    print(
        f"  wrote {dest} (from {best.path.name}, score={best.score:.1f}, "
        f"vob={vob}, t={t_sec}s){flag}"
    )
    return PackageResult(
        iso=rel_iso,
        status="ok" if not below else "ok_low_score",
        method=method,
        preview=str(dest),
        best_score=round(best.score, 2),
        below_min_accept=below,
        source_vob=vob,
        source_time_sec=t_sec,
        top_candidates=top_payload,
        error=None,
    )


def write_report(path: Path, results: list[PackageResult], cfg: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok = sum(1 for r in results if r.status.startswith("ok"))
    failed = [r for r in results if r.status not in ("ok", "ok_low_score")]
    low = [r for r in results if r.status == "ok_low_score"]
    body = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "frame_selection": cfg.get("frame_selection"),
        "min_accept_score": cfg.get("min_accept_score"),
        "summary": {
            "total": len(results),
            "ok": ok,
            "ok_low_score": len(low),
            "failed": len(failed),
        },
        "packages": [asdict(r) for r in results],
    }
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    print(f"\nReport: {path} ({ok}/{len(results)} ok, {len(low)} low-score, {len(failed)} failed)")


def submission_system(text: str) -> str | None:
    match = re.search(r"^\s*System:\s*(.+?)\s*$", text, flags=re.MULTILINE)
    return match.group(1) if match else None


def run_package(package: Path, out: Path, cfg: dict) -> dict:
    """Intake mode: menu still for one staged DVD-Video package (see module docstring)."""
    import shutil
    import tempfile

    out.mkdir(parents=True, exist_ok=True)
    for stale in ("menu_preview.jpg", "result.json"):
        (out / stale).unlink(missing_ok=True)
    result: dict = {"package": str(package), "status": None}
    sidecar = package / str(cfg.get("submission_info_filename") or "!submissionInfo.txt")
    isos = sorted(p for p in package.glob("*") if p.is_file() and p.suffix.lower() == ".iso")
    if not sidecar.is_file():
        result["status"] = "not_dvd_video"
        result["reason"] = "no submission info"
    elif submission_system(sidecar.read_text(encoding="utf-8", errors="replace")) != "DVD-Video":
        result["status"] = "not_dvd_video"
        result["reason"] = f"System: {submission_system(sidecar.read_text(encoding='utf-8', errors='replace'))}"
    elif len(isos) != 1:
        result["status"] = "skipped"
        result["reason"] = f"expected one .iso, found {len(isos)}"
    else:
        with tempfile.TemporaryDirectory(prefix="dvd-menu-") as tmp:
            if cfg_run_user():
                shutil.chown(tmp, cfg_run_user(), cfg_run_user())  # VLC runs as the preview user
            run_cfg = dict(cfg, output_root=tmp)
            try:
                res = process_one(isos[0], run_cfg)
            except Exception as exc:  # noqa: BLE001 - never block ingest on a preview
                res = None
                result["status"] = "failed"
                result["reason"] = f"{type(exc).__name__}: {exc}"
            if res is not None:
                result.update({k: v for k, v in asdict(res).items() if k not in ("preview", "top_candidates")})
                if res.preview and res.status in ("ok", "ok_low_score"):
                    shutil.copyfile(res.preview, out / "menu_preview.jpg")
                    result["preview"] = "menu_preview.jpg"
    result["generated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    (out / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"dvd-menu-preview {package.name}: {result['status']}" + (f" ({result.get('reason')})" if result.get("reason") else ""))
    return result


def main() -> None:
    args = sys.argv[1:]
    if "--package" in args:
        try:
            package = Path(args[args.index("--package") + 1])
            out = Path(args[args.index("--out") + 1])
        except (ValueError, IndexError):
            raise SystemExit("usage: extract_dvd_menu_preview.py [config.yaml] --package <dir> --out <dir>")
        positional = [a for i, a in enumerate(args) if not a.startswith("--") and (i == 0 or args[i - 1] not in ("--package", "--out"))]
        cfg = load_cfg(Path(positional[0]) if positional else DEFAULT_CFG)
        run_package(package, out, cfg)
        return

    cfg_path = Path(args[0]) if args else DEFAULT_CFG
    cfg = load_cfg(cfg_path)
    intake = Path(cfg["intake_root"])
    samples = load_sample_paths(cfg, intake)
    if not samples:
        raise SystemExit("no samples (samples or sample_list_file)")

    results: list[PackageResult] = []
    for iso in samples:
        results.append(process_one(iso, cfg))

    report = cfg.get("report_path")
    if report:
        write_report(Path(report), results, cfg)


if __name__ == "__main__":
    main()
