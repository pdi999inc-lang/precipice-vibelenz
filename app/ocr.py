"""
ocr.py - VibeLenz image text extraction with preprocessing.
"""

from __future__ import annotations

import io
import logging
import os
from typing import List

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

logger = logging.getLogger("vibelenz.ocr")

try:
    import pytesseract
    if os.name == "nt":
        pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
except Exception as e:
    pytesseract = None
    logger.warning("pytesseract unavailable: %s", e)


def _preprocess_image(image: Image.Image) -> Image.Image:
    # Convert to grayscale
    img = image.convert("L")

    # Upscale small images
    width, height = img.size
    if width < 1500:
        scale = 2
        img = img.resize((width * scale, height * scale))

    # Detect dark UI and invert if needed
    histogram = img.histogram()
    total_pixels = sum(histogram)
    dark_pixels = sum(histogram[:80])
    if total_pixels and (dark_pixels / total_pixels) > 0.45:
        img = ImageOps.invert(img)

    # Contrast and sharpen
    img = ImageEnhance.Contrast(img).enhance(2.0)
    img = img.filter(ImageFilter.SHARPEN)

    # Simple threshold
    img = img.point(lambda p: 255 if p > 140 else 0)

    return img


def extract_text_from_image(image_path: str) -> str:
    """
    OCR a single image file path and return extracted text.
    Raises exception on failure so caller can fail closed.
    """
    if pytesseract is None:
        raise RuntimeError("pytesseract is not available")

    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Image not found: {image_path}")

    with Image.open(image_path) as img:
        processed = _preprocess_image(img)
        text = pytesseract.image_to_string(processed, config="--oem 3 --psm 6")
        return text or ""


def extract_text_from_images(image_paths: List[str]) -> str:
    """
    OCR multiple image paths and join results.
    """
    chunks = []
    for path in image_paths:
        text = extract_text_from_image(path)
        if text.strip():
            chunks.append(text)
    return "\n\n".join(chunks)

