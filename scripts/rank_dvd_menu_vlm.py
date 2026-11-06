#!/usr/bin/env python3
"""Rank DVD menu candidates with a vision model and swap in the main menu.

The dvd-menu-preview step (private tool, no network) writes, per staged DVD-Video package:
  <HOST_OUTPUT>/dvd-menu/<slug>/menu_preview.jpg   heuristic pick (packed into the bag)
  <HOST_OUTPUT>/dvd-menu/<slug>/candidates/*.jpg   deduped candidate stills
  <HOST_OUTPUT>/dvd-menu/<slug>/candidates.json    [{file, vob, t_sec, score}], best score first

The heuristic score (brightness/detail) can't tell a main menu from a sub-menu, help screen or
busy gallery frame. This step asks a vision model (LiteLLM alias, config/dvd-menu-vlm.yaml) to
label each candidate main_menu / sub_menu / not_menu, and when one is a main menu with enough
confidence it becomes menu_preview.jpg. The heuristic pick is kept as menu_preview.heuristic.jpg
(re-runs rank against it) and every label goes to vlm.json.

Never fails the sweep: a disabled config, missing key, unreachable model or bad reply leaves the
heuristic pick in place. Only menu_preview.jpg is packed into the bag (build-bagit-package.py).

Usage (inside ingest-worker):
  rank_dvd_menu_vlm.py --dir /output/dvd-menu/<slug> [--config-root /config]
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

PROMPT = (
    "This is a frame from a DVD-Video disc's menu files. Is it the disc's top-level MAIN menu, a "
    "SUB-menu (a deeper menu, often with a 'main menu'/'back'/'メインメニュー' button), or NOT a menu "
    "(intro, logo, video, warning, gallery/storyboard image, transition without buttons)? Text may be "
    'Japanese. Reply with JSON only: {"kind": "main_menu"|"sub_menu"|"not_menu", "confidence": 0-1, '
    '"reason": "<12 words"}'
)
KINDS = ("main_menu", "sub_menu", "not_menu")
DEFAULTS = {"enabled": True, "timeout_seconds": 60, "min_confidence": 0.6, "max_candidates": 24}


class VlmError(RuntimeError):
    pass


def load_config(config_root: Path) -> dict[str, Any]:
    """config/dvd-menu-vlm.yaml dvd_menu_vlm block + api_key from its credentials_file.

    A missing file, block or key disables the step (with disabled_reason) instead of failing.
    """
    path = config_root / "dvd-menu-vlm.yaml"
    if not path.is_file():
        return {"enabled": False, "disabled_reason": f"no {path}"}
    block = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("dvd_menu_vlm") or {}
    cfg = {**DEFAULTS, **block}
    if not cfg.get("enabled"):
        return {**cfg, "enabled": False, "disabled_reason": "disabled in config"}
    for key in ("base_url", "model"):
        if not cfg.get(key):
            return {**cfg, "enabled": False, "disabled_reason": f"dvd_menu_vlm.{key} not set"}
    cred = config_root / str(cfg.get("credentials_file") or "secrets/litellm-credentials.yaml")
    if not cred.is_file():
        return {**cfg, "enabled": False, "disabled_reason": f"no credentials file {cred}"}
    api_key = (yaml.safe_load(cred.read_text(encoding="utf-8")) or {}).get("api_key")
    if not api_key:
        return {**cfg, "enabled": False, "disabled_reason": f"no api_key in credentials {cred}"}
    return {**cfg, "api_key": str(api_key)}


def parse_reply(text: str) -> dict[str, Any]:
    """{"kind", "confidence", "reason"} from the model's reply; anything unusable is kind=unknown."""
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        data = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        data = {}
    kind = str(data.get("kind") or "").strip()
    if kind not in KINDS:
        return {"kind": "unknown", "confidence": 0.0, "reason": (kind or (text or "").strip())[:80]}
    try:
        conf = min(max(float(data.get("confidence", 0)), 0.0), 1.0)
    except (TypeError, ValueError):
        conf = 0.0
    return {"kind": kind, "confidence": conf, "reason": str(data.get("reason") or "")[:120]}


def classify_image(cfg: dict[str, Any], image_bytes: bytes) -> dict[str, Any]:
    """One OpenAI-compatible chat call (LiteLLM). Retries once; raises VlmError on failure.

    reasoning_effort=none + allowed_openai_params: qwen3.5 is a thinking model and otherwise
    reasons until max_tokens and returns empty content (LiteLLM rejects the param for lm_studio
    unless it is explicitly allowed).
    """
    import requests

    body = {
        "model": cfg["model"],
        "temperature": 0,
        "max_tokens": 200,
        "reasoning_effort": "none",
        "allowed_openai_params": ["reasoning_effort"],
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": PROMPT},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode()}},
        ]}],
    }
    url = cfg["base_url"].rstrip("/") + "/v1/chat/completions"
    last = ""
    for _ in range(2):
        try:
            resp = requests.post(url, json=body, headers={"Authorization": f"Bearer {cfg['api_key']}"},
                                 timeout=float(cfg.get("timeout_seconds", 60)))
            if resp.ok:
                return parse_reply(resp.json()["choices"][0]["message"].get("content") or "")
            last = f"HTTP {resp.status_code}: {resp.text[:200]}"
        except (requests.RequestException, KeyError, IndexError, ValueError) as exc:
            last = f"{type(exc).__name__}: {exc}"
    raise VlmError(last)


def rank_menu_dir(
    menu_dir: Path,
    cfg: dict[str, Any],
    *,
    classify: Callable[[dict[str, Any], bytes], dict[str, Any]] = classify_image,
) -> dict[str, Any]:
    if not cfg.get("enabled"):
        return {"skipped": "disabled", "reason": cfg.get("disabled_reason"), "pick": None}
    cand_json = menu_dir / "candidates.json"
    if not cand_json.is_file():
        return {"skipped": "no candidates.json", "pick": None}
    candidates = json.loads(cand_json.read_text(encoding="utf-8"))[: int(cfg.get("max_candidates", 24))]

    preview = menu_dir / "menu_preview.jpg"
    heuristic = menu_dir / "menu_preview.heuristic.jpg"
    if heuristic.is_file():  # re-run: start from the heuristic pick, not an older VLM pick
        shutil.copyfile(heuristic, preview)

    frames: list[dict[str, Any]] = []
    errors = 0
    for idx, cand in enumerate(candidates):
        path = menu_dir / cand["file"]
        try:
            label = classify(cfg, path.read_bytes())
        except (VlmError, OSError) as exc:
            errors += 1
            label = {"kind": "error", "confidence": 0.0, "reason": str(exc)[:200]}
        frames.append({**cand, **label, "rank": idx})

    min_conf = float(cfg.get("min_confidence", 0.6))
    mains = [f for f in frames if f["kind"] == "main_menu" and f["confidence"] >= min_conf]
    # Ties go to the better heuristic score (lower rank = earlier in candidates.json).
    pick = max(mains, key=lambda f: (f["confidence"], -f["rank"])) if mains else None

    if pick:
        if preview.is_file() and not heuristic.is_file():
            shutil.copyfile(preview, heuristic)
        shutil.copyfile(menu_dir / pick["file"], preview)

    summary = {
        "model": cfg.get("model"),
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "min_confidence": min_conf,
        "pick": pick,
        "errors": errors,
        "frames": frames,
    }
    (menu_dir / "vlm.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    result_path = menu_dir / "result.json"
    if result_path.is_file():
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result["vlm_pick"] = pick["file"] if pick else None
        result["vlm_model"] = cfg.get("model")
        if pick:
            result["preview"] = "menu_preview.jpg"
        result_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return {"pick": pick, "errors": errors, "frames": len(frames)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", required=True, type=Path, help="<HOST_OUTPUT>/dvd-menu/<slug> (container path)")
    ap.add_argument("--config-root", type=Path, default=Path("/config"))
    args = ap.parse_args()
    try:
        out = rank_menu_dir(args.dir, load_config(args.config_root))
    except Exception as exc:  # noqa: BLE001 - never block ingest on a preview
        print(f"WARN dvd menu VLM ranking failed for {args.dir.name}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 0
    if out.get("skipped"):
        print(f"dvd menu VLM ranking {args.dir.name}: skipped ({out['skipped']}"
              + (f": {out['reason']}" if out.get("reason") else "") + ")")
    elif out["pick"]:
        p = out["pick"]
        print(f"dvd menu VLM ranking {args.dir.name}: main menu {p['file']} (confidence {p['confidence']:.2f}; "
              f"{out['frames']} frames, {out['errors']} errors)")
    else:
        print(f"dvd menu VLM ranking {args.dir.name}: no confident main menu; heuristic pick kept "
              f"({out['frames']} frames, {out['errors']} errors)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
