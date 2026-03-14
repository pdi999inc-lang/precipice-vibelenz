"""
ocr.py - VibeLenz image text extraction.

Strategy:
1. Primary: pytesseract (requires tesseract binary — installed via nixpacks.toml on Railway)
2. Fallback: pillow-based image-to-text stub that returns empty string gracefully
   so the system fails closed rather than crashing.

Fail-closed: any OCR exception is re-raised to caller (main.py handles it with 503).
"""

import io
import logging
from typing import List

logger = logging.getLogger("vibelenz.ocr")

try:
    import pytesseract
    from PIL import Image
    TESSERACT_AVAILABLE = True
    logger.info("pytesseract loaded successfully")
except ImportError:
    TESSERACT_AVAILABLE = False
    logger.warning("pytesseract not available — OCR will return empty results")


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


def _extract_single(image_bytes: bytes, idx: int) -> str:
    """Extract text from a single image's bytes."""
    if not TESSERACT_AVAILABLE:
        logger.warning(f"Image {idx}: tesseract unavailable, returning empty")
        return ""

    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")

    # Tesseract config: PSM 6 = assume uniform block of text (good for screenshots)
    config = "--psm 6 --oem 3"
    text = pytesseract.image_to_string(image, config=config)
    logger.info(f"Image {idx}: extracted {len(text)} chars")
    return text
