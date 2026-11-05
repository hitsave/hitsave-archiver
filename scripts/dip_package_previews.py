"""Build metadata/hitsave browse collages and grid thumbnails inside a BagIt directory."""
from __future__ import annotations

import json
import random
import re
import shutil
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path

import yaml
from PIL import Image, ImageFile, ImageOps, UnidentifiedImageError

# Allow partial reads when generating previews; bag payload files are unchanged.
ImageFile.LOAD_TRUNCATED_IMAGES = True

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
            "max_image_pixels": 536_870_912,
            "browse_preview_max_bytes_per_image": 10485760,
            "collage_min_images": 4,
            "collage_max_images": 8,
            "grid_thumbnail_max_edge": 640,
            "grid_jpeg_quality": 88,
            "display_max_edge": 2400,
            "display_jpeg_quality": 85,
            "scan_dir_names": ["Scans"],
            "scan_extensions": ["tif", "tiff"],
            "scan_max_bytes": 2147483648,
            "browse_canvas": {"medium": [640, 480], "square": [400, 400]},
            "gallery_mime_types": ["image/jpeg", "image/png", "image/gif", "image/webp"],
            "gallery_extensions": ["jpg", "jpeg", "png", "gif", "webp"],
        }
    raw = yaml.safe_load(cfg_path.read_text())
    return raw.get("dip_previews") or {}


def _is_image(rec: IndexedPayloadFile, cfg: dict) -> bool:
    mime = (rec.mime or "").lower()
    allowed_mimes = {m.lower() for m in cfg.get("gallery_mime_types") or []}
    if mime and mime in allowed_mimes:
        return True
    ext = rec.path.suffix.lower().lstrip(".")
    return ext in {e.lower() for e in cfg.get("gallery_extensions") or []}


def is_scan(rec: IndexedPayloadFile, cfg: dict) -> bool:
    """Image inside a scan folder (e.g. <game>/Scans/0001.png, any depth), or a scan_extensions file
    (TIFF) anywhere."""
    ext = rec.path.suffix.lower().lstrip(".")
    if ext and ext in {str(e).lower() for e in cfg.get("scan_extensions") or []}:
        return True
    names = {str(n).casefold() for n in cfg.get("scan_dir_names") or []}
    folders = rec.relative_path.split("/")[:-1]
    return bool(names) and any(f.casefold() in names for f in folders) and _is_image(rec, cfg)


def _gallery_eligible(rec: IndexedPayloadFile, cfg: dict) -> bool:
    max_bytes = int(cfg.get("gallery_max_bytes", 52428800))
    if is_scan(rec, cfg):
        max_bytes = max(max_bytes, int(cfg.get("scan_max_bytes", 2147483648)))
        return 0 < rec.size <= max_bytes
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


def _preview_image_error(path: Path, err: BaseException) -> None:
    print(f"  skip preview (unreadable image): {path}: {err}", file=sys.stderr)


def _apply_pillow_pixel_limit(cfg: dict) -> None:
    """Trust archiver SIPs for preview generation; cap from dip-previews.yaml."""
    raw = cfg.get("max_image_pixels")
    if raw is None:
        return
    if int(raw) <= 0:
        Image.MAX_IMAGE_PIXELS = None
        return
    Image.MAX_IMAGE_PIXELS = int(raw)


def _load_image(path: Path, max_bytes: int) -> Image.Image | None:
    try:
        if path.stat().st_size > max_bytes:
            return None
        with Image.open(path) as im:
            im.load()
            return im.convert("RGBA")
    except (OSError, ValueError, UnidentifiedImageError) as e:
        _preview_image_error(path, e)
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
    return _write_derivatives(src, dest, max_edge, quality)[0]


def _shrink(img: Image.Image, max_edge: int) -> Image.Image:
    """Fit within max_edge, never enlarging (ImageOps.contain alone upscales small images)."""
    if max(img.size) <= max_edge:
        return img
    return ImageOps.contain(img, (max_edge, max_edge), method=Image.Resampling.LANCZOS)


def _write_derivatives(
    src: Path,
    grid_dest: Path,
    grid_edge: int,
    grid_quality: int,
    display_dest: Path | None = None,
    display_edge: int = 2400,
    display_quality: int = 85,
) -> tuple[bool, bool]:
    """Grid thumbnail and (optionally) a web-size display JPEG from ONE decode of src.

    Scans can be 100+ MB PNGs: decode once, shrink to the display size, and make the grid thumb
    from that, instead of decoding the original twice. Returns (grid_ok, display_ok).
    """
    grid_dest.parent.mkdir(parents=True, exist_ok=True)
    made: list[Path] = []
    try:
        with Image.open(src) as im:
            im.load()
            rgb = im.convert("RGB")
        try:
            display = _shrink(rgb, display_edge) if display_dest is not None else rgb
        finally:
            if display_dest is not None and display is not rgb:
                rgb.close()
        try:
            if display_dest is not None:
                display_dest.parent.mkdir(parents=True, exist_ok=True)
                display.save(display_dest, "JPEG", quality=display_quality, optimize=True, progressive=True)
                made.append(display_dest)
            thumb = _shrink(display, grid_edge)
            try:
                thumb.save(grid_dest, "JPEG", quality=grid_quality, optimize=True)
                made.append(grid_dest)
            finally:
                if thumb is not display:
                    thumb.close()
        finally:
            display.close()
    except (OSError, ValueError, UnidentifiedImageError) as e:
        _preview_image_error(src, e)
        for path in (grid_dest, display_dest):
            if path is not None and path.is_file():
                path.unlink()
        return False, False

    return grid_dest in made, display_dest is not None and display_dest in made


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
    _apply_pillow_pixel_limit(cfg)
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
        "display": {},
        # key -> payload file (bag-relative path, bytes, scan flag): gallery labels + scan listing.
        "files": {
            f.key: {"path": f"{payload_dir}/{f.relative_path}", "size": f.size, "scan": is_scan(f, cfg)}
            for f in gallery
        },
        "gallery_file_keys": [f.key for f in gallery],
    }

    max_edge = int(cfg.get("grid_thumbnail_max_edge", 640))
    quality = int(cfg.get("grid_jpeg_quality", 88))
    display_edge = int(cfg.get("display_max_edge", 2400))
    display_quality = int(cfg.get("display_jpeg_quality", 85))
    for gf in gallery:
        rel_inner = f"{metadata_dir}/hitsave/thumbnails/grid/{gf.key}.jpg"
        rel_display = f"{metadata_dir}/hitsave/display/{gf.key}.jpg"
        grid_ok, display_ok = _write_derivatives(
            gf.path, bag_dir / rel_inner, max_edge, quality,
            bag_dir / rel_display, display_edge, display_quality,
        )
        if grid_ok:
            manifest["grid"][gf.key] = rel_inner
        if display_ok:
            manifest["display"][gf.key] = rel_display

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


def write_dvd_menu_packaged_previews(
    bag_dir: Path,
    *,
    menu_jpg: Path,
    package_id: str,
    metadata_dir: str = "metadata",
    config: dict | None = None,
) -> dict:
    """Pack a single DVD menu JPEG as metadata/hitsave browse + grid previews."""
    cfg = config or load_preview_config()
    _apply_pillow_pixel_limit(cfg)
    meta_hitsave = bag_dir / metadata_dir / "hitsave"
    meta_hitsave.mkdir(parents=True, exist_ok=True)

    grid_key = "menu"
    grid_rel = f"{metadata_dir}/hitsave/thumbnails/grid/{grid_key}.jpg"
    grid_dest = bag_dir / grid_rel
    max_edge = int(cfg.get("grid_thumbnail_max_edge", 640))
    quality = int(cfg.get("grid_jpeg_quality", 88))
    if not _write_grid_thumb(menu_jpg, grid_dest, max_edge, quality):
        raise RuntimeError(f"Could not build grid thumb from {menu_jpg}")

    with Image.open(menu_jpg) as im:
        im.load()
        base = im.convert("RGBA")

    canvases = cfg.get("browse_canvas") or {}
    browse: dict[str, str] = {}
    for type_name, size in (
        ("medium", canvases.get("medium", [640, 480])),
        ("square", canvases.get("square", [400, 400])),
    ):
        w, h = int(size[0]), int(size[1])
        fitted = _scale_to_fit(base, w, h)
        canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ox = (w - fitted.width) // 2
        oy = (h - fitted.height) // 2
        canvas.alpha_composite(fitted, (ox, oy))
        rel = f"{metadata_dir}/hitsave/browse-{type_name}.png"
        canvas.save(bag_dir / rel, "PNG", optimize=True)
        canvas.close()
        browse[type_name] = rel.replace("\\", "/")

    base.close()

    manifest: dict = {
        "manifest_version": MANIFEST_VERSION,
        "package_id": package_id,
        "browse": browse,
        "grid": {grid_key: grid_rel.replace("\\", "/")},
        "gallery_file_keys": [grid_key],
        "dvd_menu_preview": True,
    }
    manifest_path = bag_dir / str(cfg.get("manifest_inner_path") or "metadata/hitsave/preview-manifest.json")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


_PAYLOAD_KEY = re.compile(r"f\d+")


def merge_menu_manifest(menu_manifest: dict, existing: dict | None) -> dict:
    """Menu manifest + an existing manifest's payload previews (f<N>: scans etc.), menu first.

    The menu stays the browse image, as on every DVD; payload grid/display/files entries are kept
    as they are (their files already exist in the bag).
    """
    if not existing:
        return menu_manifest
    grid = existing.get("grid") or {}
    payload_keys = [
        k for k in (existing.get("gallery_file_keys") or list(grid))
        if _PAYLOAD_KEY.fullmatch(str(k)) and k in grid
    ]
    if not payload_keys:
        return menu_manifest
    merged = dict(menu_manifest)
    merged["grid"] = {**menu_manifest.get("grid", {}), **{k: grid[k] for k in payload_keys}}
    display = {**(menu_manifest.get("display") or {}),
               **{k: v for k, v in (existing.get("display") or {}).items() if k in payload_keys}}
    if display:
        merged["display"] = display
    files = {k: v for k, v in (existing.get("files") or {}).items() if k in payload_keys}
    if files:
        merged["files"] = files
    merged["gallery_file_keys"] = list(menu_manifest.get("gallery_file_keys") or []) + payload_keys
    return merged


def add_dvd_menu_preview(
    bag_dir: Path,
    *,
    menu_jpg: Path,
    package_id: str,
    payload_manifest: dict | None,
    metadata_dir: str = "metadata",
    config: dict | None = None,
) -> dict:
    """Pack a DVD menu still (dvd-menu-preview) in front of the payload previews; returns the manifest."""
    cfg = config or load_preview_config()
    menu = write_dvd_menu_packaged_previews(
        bag_dir, menu_jpg=menu_jpg, package_id=package_id, metadata_dir=metadata_dir, config=cfg
    )
    display_rel = f"{metadata_dir}/hitsave/display/menu.jpg"
    (bag_dir / display_rel).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(menu_jpg, bag_dir / display_rel)  # menu stills are already web size (720x480)
    menu["display"] = {"menu": display_rel}
    merged = merge_menu_manifest(menu, payload_manifest)
    manifest_path = bag_dir / str(cfg.get("manifest_inner_path") or "metadata/hitsave/preview-manifest.json")
    manifest_path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    return merged
