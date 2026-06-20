"""
image_loader.py — Load, validate, and preprocess images before sending to the model.

Handles:
  • Splitting semicolon-separated image paths from the CSV
  • File-existence and readability checks
  • Dimension and file-size validation
  • Resizing (longest edge capped at MAX_IMAGE_DIMENSION)
  • Colour-mode normalisation (→ RGB)
  • Base64 JPEG encoding for Groq fallback
"""

from __future__ import annotations

import base64
import io
import logging
from pathlib import Path

from PIL import Image
from PIL.ExifTags import TAGS

from fraud_cache import global_fraud_cache

from config import (
    JPEG_QUALITY,
    MAX_IMAGE_DIMENSION,
    MIN_IMAGE_DIMENSION,
    MIN_IMAGE_FILE_SIZE,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def resize_image(img: Image.Image, max_dim: int) -> Image.Image:
    """Resize so the longest edge equals *max_dim*, maintaining aspect ratio.

    Uses ``Image.Resampling.LANCZOS`` for high-quality down-sampling.
    If the image is already within bounds it is returned unchanged.

    Args:
        img: Source PIL image.
        max_dim: Maximum allowed pixel count for the longest edge.

    Returns:
        A (possibly new) PIL Image with longest edge ≤ *max_dim*.
    """
    width, height = img.size
    longest = max(width, height)

    if longest <= max_dim:
        return img

    scale = max_dim / longest
    new_width = int(width * scale)
    new_height = int(height * scale)

    return img.resize((new_width, new_height), Image.Resampling.LANCZOS)


def image_to_base64(img: Image.Image, quality: int = 85) -> str:
    """Convert a PIL Image to a base64-encoded JPEG string.

    Args:
        img: PIL Image in RGB mode.
        quality: JPEG compression quality (1–95).

    Returns:
        A plain base64 string (no ``data:`` URI prefix).
    """
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=quality)
    buffer.seek(0)
    return base64.b64encode(buffer.read()).decode("utf-8")


# ---------------------------------------------------------------------------
# Main public function
# ---------------------------------------------------------------------------

def load_and_validate_images(image_paths_str: str, dataset_dir: Path, user_id: str = "") -> dict:
    """Load, validate, and preprocess every image referenced by a claim row.

    Takes the **semicolon-separated** ``image_paths`` string that comes
    straight from the CSV and resolves each path relative to *dataset_dir*.

    Processing pipeline (per image):
      1. Split *image_paths_str* on ``';'``
      2. Resolve the path relative to *dataset_dir*
      3. Check the file exists on disk
      4. Attempt to open with PIL (catches corrupt / unreadable files)
      5. Reject if **either** dimension < ``MIN_IMAGE_DIMENSION``
      6. Reject if file size < ``MIN_IMAGE_FILE_SIZE``
      7. Resize if longest edge > ``MAX_IMAGE_DIMENSION`` (LANCZOS)
      8. Convert to RGB (handles RGBA, P, LA, etc.)
      9. Encode as base64 JPEG for Groq fallback

    Args:
        image_paths_str: Semicolon-separated relative image paths from the CSV.
        dataset_dir: Root directory that image paths are relative to.

    Returns:
        A dict with three keys:

        * ``'loaded'`` – list of dicts, each containing:
            - ``'image_id'``: filename without extension (e.g. ``'img_1'``)
            - ``'pil_image'``: the preprocessed ``PIL.Image.Image``
            - ``'base64'``: base64-encoded JPEG string
            - ``'original_path'``: the path as it appeared in the CSV
        * ``'failed'`` – list of dicts with ``'image_id'`` and ``'reason'``
        * ``'all_failed'`` – ``True`` when zero images loaded successfully
    """
    loaded: list[dict] = []
    failed: list[dict] = []

    raw_paths = [p.strip() for p in image_paths_str.split(";") if p.strip()]

    for raw_path in raw_paths:
        image_id = Path(raw_path).stem
        resolved = dataset_dir / raw_path

        # --- 3. File existence --------------------------------------------------
        if not resolved.is_file():
            reason = f"File not found: {resolved}"
            logger.warning("Image %s — %s", image_id, reason)
            failed.append({"image_id": image_id, "reason": reason})
            continue

        # --- 6. File-size check (before opening) --------------------------------
        file_size = resolved.stat().st_size
        if file_size < MIN_IMAGE_FILE_SIZE:
            reason = (
                f"File too small ({file_size} bytes, "
                f"minimum {MIN_IMAGE_FILE_SIZE} bytes)"
            )
            logger.warning("Image %s — %s", image_id, reason)
            failed.append({"image_id": image_id, "reason": reason})
            continue

        # --- 4. Open with PIL and Extract EXIF ------------------------------------
        exif_data = {}
        is_duplicate_fraud = False
        try:
            # Hash checking for duplicate image fraud
            image_bytes = resolved.read_bytes()
            img_hash = global_fraud_cache.compute_hash(image_bytes)
            if user_id and global_fraud_cache.check_and_add(img_hash, user_id):
                is_duplicate_fraud = True
                logger.warning("Image %s is a duplicate submitted by a different user!", image_id)

            img = Image.open(resolved)
            img.load()  # Force full decode so corrupt files raise here
            
            # Extract basic EXIF safely
            exif_raw = img.getexif()
            if exif_raw:
                for tag_id, value in exif_raw.items():
                    tag = TAGS.get(tag_id, tag_id)
                    if tag in ("DateTimeOriginal", "DateTime", "Software", "Make", "Model"):
                        exif_data[tag] = str(value).strip()
        except Exception as exc:
            reason = f"Cannot open image: {exc}"
            logger.warning("Image %s — %s", image_id, reason)
            failed.append({"image_id": image_id, "reason": reason})
            continue

        # --- 5. Minimum dimension check -----------------------------------------
        width, height = img.size
        if width < MIN_IMAGE_DIMENSION or height < MIN_IMAGE_DIMENSION:
            reason = (
                f"Image too small ({width}×{height}), "
                f"minimum {MIN_IMAGE_DIMENSION}×{MIN_IMAGE_DIMENSION}"
            )
            logger.warning("Image %s — %s", image_id, reason)
            failed.append({"image_id": image_id, "reason": reason})
            continue

        # --- 7. Resize if needed ------------------------------------------------
        img = resize_image(img, MAX_IMAGE_DIMENSION)

        # --- 8. Convert to RGB --------------------------------------------------
        if img.mode != "RGB":
            img = img.convert("RGB")

        # --- 9. Base64 encode ---------------------------------------------------
        b64 = image_to_base64(img, quality=JPEG_QUALITY)

        loaded.append(
            {
                "image_id": image_id,
                "pil_image": img,
                "base64": b64,
                "original_path": raw_path,
                "exif_data": exif_data,
                "is_duplicate_fraud": is_duplicate_fraud,
            }
        )
        logger.debug(
            "Image %s loaded OK (%dx%d)", image_id, img.size[0], img.size[1]
        )

    all_failed = len(loaded) == 0

    if all_failed and raw_paths:
        logger.error(
            "All %d image(s) failed to load for paths: %s",
            len(raw_paths),
            image_paths_str,
        )

    return {
        "loaded": loaded,
        "failed": failed,
        "all_failed": all_failed,
    }
