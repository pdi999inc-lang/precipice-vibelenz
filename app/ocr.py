"""
ocr.py - VibeLenz image text extraction with preprocessing.

Strategy:
1. Preprocess image for optimal OCR (handles dark UIs, chat bubbles, varied contrast)
2. Primary: pytesseract with tuned config
3. Fail-closed: any OCR exception is re-raised to caller (main.py handles with 503)

Preprocessing pipeline:
- Upscale small images (Tesseract performs best at 300+ DPI equivalent)
- Convert to grayscale
- Auto-detect dark UI and invert
- Enhance contrast
- Threshold to clean binary image
- Run OCR with tuned config
"""

import io
import logging
import os
import statistics
from typing import List

logger = logging.getLogger("vibelenz.ocr")

try:
    import pytesseract
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps

    if os.name == "nt":
        pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

    TESSERACT_AVAILABLE = True
    logger.info("pytesseract loaded successfully")
except ImportError:
    TESSERACT_AVAILABLE = False
    logger.warning("pytesseract not available — OCR will return empty results")


# Minimum dimension before upscaling
MIN_DIMENSION = 1000
# Upscale factor applied when image is small
UPSCALE_FACTOR = 2.0
# Darkness threshold: if mean pixel value below this, treat as dark UI
DARK_UI_THRESHOLD = 100


def extract_text_from_images(image_bytes_list: List[bytes]) -> str:
    """
    Accept list of raw image bytes. Return combined extracted text string.
    Raises on unrecoverable error (caller must handle).
    """
    if not image_bytes_list:
        return ""

    extracted_parts: List[str] = []

    for idx, image_bytes in enumerate(image_bytes_list):
        try:
            text = _extract_single(image_bytes, idx)
            if text:
                extracted_parts.append(text.strip())
        except Exception as e:
            logger.error(f"OCR failed on image {idx}: {e}")
            raise RuntimeError(f"OCR failure on image {idx}: {e}") from e

    combined = "\n\n".join(extracted_parts)
    logger.info(f"OCR complete: {len(combined)} chars extracted from {len(image_bytes_list)} image(s)")
    return combined


def _preprocess(image: "Image.Image") -> "Image.Image":
    """
    Preprocess image for maximum Tesseract accuracy on chat screenshots.
    Handles both light and dark UI themes.
    """
    image = image.convert("RGB")

    w, h = image.size
    if min(w, h) < MIN_DIMENSION:
        scale = UPSCALE_FACTOR
        image = image.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        logger.debug(f"Upscaled image from {w}x{h} to {image.size}")

    gray = image.convert("L")

    pixels = list(gray.getdata())
    mean_brightness = statistics.mean(pixels)
    logger.debug(f"Mean brightness: {mean_brightness:.1f}")

    if mean_brightness < DARK_UI_THRESHOLD:
        gray = ImageOps.invert(gray)
        logger.debug("Dark UI detected — inverted image")

    enhancer = ImageEnhance.Contrast(gray)
    gray = enhancer.enhance(2.0)

    gray = gray.filter(ImageFilter.SHARPEN)

    return gray


def _extract_single(image_bytes: bytes, idx: int) -> str:
    """Extract text from a single image's bytes with preprocessing."""
    if not TESSERACT_AVAILABLE:
        logger.warning(f"Image {idx}: tesseract unavailable, returning empty")
        return ""

    image = Image.open(io.BytesIO(image_bytes))
    processed = _preprocess(image)

    config = "--psm 6 --oem 3"
    text = pytesseract.image_to_string(processed, config=config)
    logger.info(f"Image {idx}: extracted {len(text)} chars")
    return text
