from __future__ import annotations

from pathlib import Path

from PIL import Image

from src.utils.console import log

MAX_COVER_SIZE = 10_000_000
MAX_COVER_DIMENSION = 500


def resize_cover_image(src_path: Path, dest_dir: Path) -> Path:
    try:
        with Image.open(src_path) as img:
            needs_resize = img.size[0] > MAX_COVER_DIMENSION or img.size[1] > MAX_COVER_DIMENSION
            img = img.convert("RGB")
            if needs_resize:
                img.thumbnail((MAX_COVER_DIMENSION, MAX_COVER_DIMENSION), Image.LANCZOS)
            out = dest_dir / f"{src_path.stem}_resized.jpg"
            img.save(out, "JPEG", quality=85, optimize=True)
            return out
    except Exception as e:
        log.warning(f"Failed to resize cover image {src_path}: {e}")
    return src_path
