"""VLM ranking of DVD menu candidates: swaps menu_preview.jpg only for a confident main_menu."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rank_dvd_menu_vlm as rank  # noqa: E402

CFG = {"enabled": True, "base_url": "http://vlm:4000", "model": "hitsave-menu-vlm", "timeout_seconds": 5,
       "min_confidence": 0.6, "max_candidates": 24, "api_key": "k"}


def _menu_dir(tmp: Path, names: list[str]) -> Path:
    d = tmp / "dvd-menu" / "dvd-video-x"
    (d / "candidates").mkdir(parents=True)
    (d / "menu_preview.jpg").write_bytes(b"heuristic")
    rows = []
    for i, n in enumerate(names):
        (d / "candidates" / n).write_bytes(n.encode())
        rows.append({"file": f"candidates/{n}", "vob": "VTS_01_0.VOB", "t_sec": i, "score": 100.0 - i})
    (d / "candidates.json").write_text(json.dumps(rows))
    (d / "result.json").write_text(json.dumps({"status": "ok", "preview": "menu_preview.jpg"}))
    return d


def _fake(labels: dict[str, dict]):
    def classify(cfg, image_bytes):
        return labels[image_bytes.decode()]
    return classify


def test_swaps_to_most_confident_main_menu(tmp_path: Path) -> None:
    d = _menu_dir(tmp_path, ["a.jpg", "b.jpg", "c.jpg"])
    labels = {"a.jpg": {"kind": "sub_menu", "confidence": 0.9},
              "b.jpg": {"kind": "main_menu", "confidence": 0.7},
              "c.jpg": {"kind": "main_menu", "confidence": 0.95}}
    out = rank.rank_menu_dir(d, CFG, classify=_fake(labels))
    assert out["pick"]["file"] == "candidates/c.jpg"
    assert (d / "menu_preview.jpg").read_bytes() == b"c.jpg"
    assert (d / "menu_preview.heuristic.jpg").read_bytes() == b"heuristic"
    vlm = json.loads((d / "vlm.json").read_text())
    assert vlm["model"] == "hitsave-menu-vlm" and len(vlm["frames"]) == 3
    assert json.loads((d / "result.json").read_text())["vlm_pick"] == "candidates/c.jpg"


def test_keeps_heuristic_when_no_confident_main_menu(tmp_path: Path) -> None:
    d = _menu_dir(tmp_path, ["a.jpg", "b.jpg"])
    labels = {"a.jpg": {"kind": "not_menu", "confidence": 0.9}, "b.jpg": {"kind": "main_menu", "confidence": 0.4}}
    out = rank.rank_menu_dir(d, CFG, classify=_fake(labels))
    assert out["pick"] is None
    assert (d / "menu_preview.jpg").read_bytes() == b"heuristic"
    assert not (d / "menu_preview.heuristic.jpg").exists()


def test_errors_never_raise_and_keep_heuristic(tmp_path: Path) -> None:
    d = _menu_dir(tmp_path, ["a.jpg"])

    def boom(cfg, image_bytes):
        raise rank.VlmError("connection refused")

    out = rank.rank_menu_dir(d, CFG, classify=boom)
    assert out["pick"] is None and out["errors"] == 1
    assert (d / "menu_preview.jpg").read_bytes() == b"heuristic"


def test_tie_prefers_higher_heuristic_score(tmp_path: Path) -> None:
    d = _menu_dir(tmp_path, ["a.jpg", "b.jpg"])  # a has the higher heuristic score
    labels = {"a.jpg": {"kind": "main_menu", "confidence": 0.8}, "b.jpg": {"kind": "main_menu", "confidence": 0.8}}
    assert rank.rank_menu_dir(d, CFG, classify=_fake(labels))["pick"]["file"] == "candidates/a.jpg"


def test_rerun_ranks_against_original_heuristic(tmp_path: Path) -> None:
    d = _menu_dir(tmp_path, ["a.jpg", "b.jpg"])
    rank.rank_menu_dir(d, CFG, classify=_fake({"a.jpg": {"kind": "main_menu", "confidence": 0.9},
                                                "b.jpg": {"kind": "not_menu", "confidence": 0.9}}))
    rank.rank_menu_dir(d, CFG, classify=_fake({"a.jpg": {"kind": "not_menu", "confidence": 0.9},
                                                "b.jpg": {"kind": "not_menu", "confidence": 0.9}}))
    assert (d / "menu_preview.jpg").read_bytes() == b"heuristic"  # restored, not left on the old VLM pick


def test_noop_without_candidates_or_when_disabled(tmp_path: Path) -> None:
    d = tmp_path / "empty"; d.mkdir()
    assert rank.rank_menu_dir(d, CFG, classify=_fake({}))["skipped"] == "no candidates.json"
    d2 = _menu_dir(tmp_path, ["a.jpg"])
    assert rank.rank_menu_dir(d2, {**CFG, "enabled": False}, classify=_fake({}))["skipped"] == "disabled"


def test_parse_reply_handles_fences_and_garbage() -> None:
    assert rank.parse_reply('```json\n{"kind": "main_menu", "confidence": 0.9, "reason": "x"}\n```')["kind"] == "main_menu"
    assert rank.parse_reply("no idea")["kind"] == "unknown"
    assert rank.parse_reply('{"kind": "banana", "confidence": 2}') == {"kind": "unknown", "confidence": 0.0, "reason": "banana"}


def test_load_config_without_key_disables(tmp_path: Path) -> None:
    (tmp_path / "dvd-menu-vlm.yaml").write_text(
        "dvd_menu_vlm:\n  enabled: true\n  base_url: http://h:4000\n  model: m\n  credentials_file: secrets/litellm-credentials.yaml\n")
    cfg = rank.load_config(tmp_path)
    assert cfg["enabled"] is False and "credentials" in cfg["disabled_reason"]
