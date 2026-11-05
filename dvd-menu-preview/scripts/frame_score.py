"""Heuristic ranking for DVD menu stills (no ML / vision model)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageStat


@dataclass(frozen=True)
class FrameScore:
    path: Path
    score: float
    mean_luma: float
    laplacian_variance: float
    rejected: bool
    reject_reason: str | None


def laplacian_variance(img: Image.Image) -> float:
    gray = img.convert("L")
    w, h = gray.size
    if w < 3 or h < 3:
        return 0.0
    px = gray.load()
    acc = 0.0
    n = 0
    for y in range(1, h - 1):
        for x in range(1, w - 1):
            c = px[x, y]
            lap = abs(4 * c - px[x - 1, y] - px[x + 1, y] - px[x, y - 1] - px[x, y + 1])
            acc += lap * lap
            n += 1
    return acc / max(n, 1)


def score_frame(path: Path, cfg: dict) -> FrameScore:
    sel = cfg.get("frame_selection") or {}
    min_mean = float(sel.get("min_mean_luma", 12))
    mean_penalty = float(sel.get("mean_penalty", 0.05))
    try:
        with Image.open(path) as im:
            stat = ImageStat.Stat(im.convert("L"))
            mean = float(stat.mean[0])
            lap = laplacian_variance(im)
    except OSError as exc:
        return FrameScore(
            path=path,
            score=-1.0,
            mean_luma=0.0,
            laplacian_variance=0.0,
            rejected=True,
            reject_reason=str(exc),
        )

    if mean < min_mean:
        return FrameScore(
            path=path,
            score=-1.0,
            mean_luma=mean,
            laplacian_variance=lap,
            rejected=True,
            reject_reason=f"mean_luma {mean:.1f} < {min_mean}",
        )

    total = lap - abs(mean - 128) * mean_penalty
    return FrameScore(
        path=path,
        score=total,
        mean_luma=mean,
        laplacian_variance=lap,
        rejected=False,
        reject_reason=None,
    )


def pick_best_frame(scored: list[FrameScore], cfg: dict) -> FrameScore | None:
    """Prefer menu-like brightness; sharp black splashes (age gates, fades) lose."""
    sel = cfg.get("frame_selection") or {}
    menu_floor = float(sel.get("min_menu_mean_luma", 50))
    usable = [s for s in scored if not s.rejected and s.score >= 0]
    if not usable:
        return None
    bright = [s for s in usable if s.mean_luma >= menu_floor]
    pool = bright if bright else usable
    return max(pool, key=lambda s: s.score)


def parse_still_source(still: Path) -> tuple[str | None, int | None]:
    """…/candidates/VTS_01_0_VOB/menu_t08.jpg → (VTS_01_0.VOB, 8)."""
    parent = still.parent.name
    vob = parent.replace("_VOB", ".VOB") if parent.endswith("_VOB") else None
    t_sec = None
    if still.stem.startswith("menu_t") and len(still.stem) >= 8:
        try:
            t_sec = int(still.stem[6:8])
        except ValueError:
            pass
    return vob, t_sec
