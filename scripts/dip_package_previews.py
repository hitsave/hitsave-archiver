"""Build metadata/hitsave browse collages and grid thumbnails inside a BagIt directory."""
from __future__ import annotations

import json
import random
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path

import yaml
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CFG_PATH = ROOT / "config" / "preservation" / "dip-previews.yaml"

MANIFEST_VERSION = 1
CARD_WIDTH_RATIO = 2.5
CARD_HEIGHT_RATIO = 3.5
TILE_GAP_PX = 6


@dataclass
class IndexedPayloadFile:
    key: str
    relative_path: str
    path: Path
    size: int
    mime: str


def load_preview_config(path: Path | None = None) -> dict:
    cfg_path = path or DEFAULT_CFG_PATH
    if not cfg_path.is_file():
        alt = Path("/config/preservation/dip-previews.yaml")
        cfg_path = alt if alt.is_file() else cfg_path
    if not cfg_path.is_file():
        return {
            "gallery_max_bytes": 52428800,
            "browse_preview_max_bytes_per_image": 10485760,
            "collage_min_images": 4,
            "collage_max_images": 8,
            "grid_thumbnail_max_edge": 640,
            "grid_jpeg_quality": 88,
            "browse_canvas": {"medium": [640, 480], "square": [400, 400]},
            "gallery_mime_types": ["image/jpeg", "image/png", "image/gif", "image/webp"],
            "gallery_extensions": ["jpg", "jpeg", "png", "gif", "webp"],
        }
    raw = yaml.safe_load(cfg_path.read_text())
    return raw.get("dip_previews") or {}


def _gallery_eligible(rec: IndexedPayloadFile, cfg: dict) -> bool:
    max_bytes = int(cfg.get("gallery_max_bytes", 52428800))
    if rec.size <= 0 or rec.size > max_bytes:
        return False
    mime = (rec.mime or "").lower()
    allowed_mimes = {m.lower() for m in cfg.get("gallery_mime_types") or []}
    if mime and mime in allowed_mimes:
        return True
    ext = rec.path.suffix.lower().lstrip(".")
    allowed_ext = {e.lower() for e in cfg.get("gallery_extensions") or []}
    return ext in allowed_ext


def index_payload_files(bag_dir: Path, payload_dir: str) -> list[IndexedPayloadFile]:
    data_root = bag_dir / payload_dir
    if not data_root.is_dir():
        return []
    rows: list[tuple[str, Path, int, str]] = []
    for path in sorted(data_root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(data_root).as_posix()
        import mimetypes

        mime, _ = mimetypes.guess_type(path.name)
        rows.append((rel, path, path.stat().st_size, mime or "application/octet-stream"))
    out: list[IndexedPayloadFile] = []
    for i, (rel, path, size, mime) in enumerate(rows):
        out.append(
            IndexedPayloadFile(
                key=f"f{i}",
                relative_path=rel,
                path=path,
                size=size,
                mime=mime,
            )
        )
    return out


def _seed_pick(items: list[IndexedPayloadFile], count: int, package_id: str, salt: str) -> list[IndexedPayloadFile]:
    if count >= len(items):
        return list(items)
    seed = zlib.crc32(f"{package_id}:{salt}".encode()) & 0xFFFFFFFF
    rng = random.Random(seed)
    shuffled = list(items)
    rng.shuffle(shuffled)
    return shuffled[:count]


def _pick_collage_count(available: int, package_id: str, cfg: dict) -> int:
    min_n = int(cfg.get("collage_min_images", 4))
    max_n = min(int(cfg.get("collage_max_images", 8)), available)
    if available < min_n:
        return available
    if max_n <= min_n:
        return max_n
    seed = zlib.crc32(f"{package_id}:collage-count".encode()) & 0xFFFFFFFF
    span = max_n - min_n + 1
    return min_n + (seed % span)


def _scale_to_fit(img: Image.Image, max_w: int, max_h: int) -> Image.Image:
    img = img.convert("RGBA")
    return ImageOps.contain(img, (max_w, max_h), method=Image.Resampling.LANCZOS)


def _load_image(path: Path, max_bytes: int) -> Image.Image | None:
    try:
        if path.stat().st_size > max_bytes:
            return None
        with Image.open(path) as im:
            return im.convert("RGBA")
    except OSError:
        return None


def _build_collage(images: list[Image.Image], canvas_w: int, canvas_h: int) -> Image.Image:
    canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    if not images:
        return canvas
    top_count = (len(images) + 1) // 2
    rows = [images[:top_count], images[top_count:]]
    centers_y = [int(canvas_h * 0.26), int(canvas_h * 0.74)]
    for row_imgs, center_y in zip(rows, centers_y):
        if not row_imgs:
            continue
        _layout_row(canvas, row_imgs, center_y, canvas_w, canvas_h)
    return canvas


def _layout_row(canvas: Image.Image, images: list[Image.Image], center_y: int, canvas_w: int, canvas_h: int) -> None:
    max_usable = int(canvas_w * 0.99)
    row_band = int(canvas_h * 0.5)
    lo, hi = 36, max(36, int(row_band * 0.98))
    best_tiles: list[tuple[Image.Image, int, int]] | None = None
    best_total = 0
    n = len(images)
    while lo <= hi:
        card_h = (lo + hi) // 2
        card_w = int(round(card_h * (CARD_WIDTH_RATIO / CARD_HEIGHT_RATIO)))
        tiles: list[tuple[Image.Image, int, int]] = []
        for i, photo in enumerate(images):
            face = _scale_to_fit(photo, card_w, card_h)
            angle = -10.0 + (20.0 * i / (n - 1)) if n > 1 else 0.0
            rotated = face.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC)
            tiles.append((rotated, rotated.width, rotated.height))
        total_w = sum(w for _, w, _ in tiles) + TILE_GAP_PX * max(0, len(tiles) - 1)
        if total_w <= max_usable:
            best_tiles = tiles
            best_total = total_w
            lo = card_h + 1
        else:
            for tile, _, _ in tiles:
                tile.close()
            hi = card_h - 1
    if not best_tiles:
        return
    x = int((canvas_w - best_total) / 2)
    for tile, w, h in best_tiles:
        y = int(center_y - h / 2)
        canvas.alpha_composite(tile, (x, y))
        tile.close()
        x += w + TILE_GAP_PX


def _write_grid_thumb(src: Path, dest: Path, max_edge: int, quality: int) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(src) as im:
            im = im.convert("RGB")
            im = ImageOps.contain(im, (max_edge, max_edge), method=Image.Resampling.LANCZOS)
            im.save(dest, "JPEG", quality=quality, optimize=True)
    except OSError as e:
        print(f"  skip grid thumb (unreadable image): {src.name}: {e}", file=sys.stderr)
        if dest.is_file():
            dest.unlink()
        return False

    return True


def write_hitsave_previews(
    bag_dir: Path,
    *,
    package_id: str,
    payload_dir: str = "data",
    metadata_dir: str = "metadata",
    config: dict | None = None,
) -> dict | None:
    """Create metadata/hitsave previews; return manifest dict or None if no gallery images."""
    cfg = config or load_preview_config()
    meta_hitsave = bag_dir / metadata_dir / "hitsave"
    meta_hitsave.mkdir(parents=True, exist_ok=True)

    indexed = index_payload_files(bag_dir, payload_dir)
    browse_max = int(cfg.get("browse_preview_max_bytes_per_image", 10485760))
    gallery = [f for f in indexed if _gallery_eligible(f, cfg)]
    if not gallery:
        return None

    manifest: dict = {
        "manifest_version": MANIFEST_VERSION,
        "package_id": package_id,
        "browse": {},
        "grid": {},
        "gallery_file_keys": [f.key for f in gallery],
    }

    grid_dir = meta_hitsave / "thumbnails" / "grid"
    max_edge = int(cfg.get("grid_thumbnail_max_edge", 640))
    quality = int(cfg.get("grid_jpeg_quality", 88))
    for gf in gallery:
        rel_inner = f"{metadata_dir}/hitsave/thumbnails/grid/{gf.key}.jpg"
        dest = bag_dir / rel_inner
        if _write_grid_thumb(gf.path, dest, max_edge, quality):
            manifest["grid"][gf.key] = rel_inner.replace("\\", "/")

    pick_n = _pick_collage_count(len(gallery), package_id, cfg)
    picked = _seed_pick(gallery, pick_n, package_id, "collage-pick")
    loaded: list[Image.Image] = []
    for gf in picked:
        img = _load_image(gf.path, browse_max)
        if img is not None:
            loaded.append(img)
    min_collage = int(cfg.get("collage_min_images", 4))
    canvases = cfg.get("browse_canvas") or {}
    for type_name, size in (("medium", canvases.get("medium", [640, 480])), ("square", canvases.get("square", [400, 400]))):
        w, h = int(size[0]), int(size[1])
        if len(loaded) >= min_collage:
            collage = _build_collage(loaded, w, h)
        elif loaded:
            collage = _scale_to_fit(loaded[0], w, h)
            canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            ox = (w - collage.width) // 2
            oy = (h - collage.height) // 2
            canvas.alpha_composite(collage, (ox, oy))
            collage = canvas
        else:
            continue
        rel = f"{metadata_dir}/hitsave/browse-{type_name}.png"
        out_path = bag_dir / rel
        collage.save(out_path, "PNG", optimize=True)
        collage.close()
        manifest["browse"][type_name] = rel.replace("\\", "/")

    for img in loaded:
        img.close()

    manifest_path = bag_dir / str(cfg.get("manifest_inner_path") or "metadata/hitsave/preview-manifest.json")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest
