""" ocr.py - VibeLenz image text extraction with preprocessing.

Architecture: New (Option A)
  Public interface: extract_text_from_image(image_path: str) -> str
  Called by api.py with a temp file path written by routes.py.

Strategy:
1. Preprocess image for optimal OCR (handles dark UIs, chat bubbles, varied contrast)
2. Primary: pytesseract with tuned config
3. Fail-closed: any OCR exception is re-raised to caller (api.py handles with structured error)

Preprocessing pipeline:
- Upscale small images (Tesseract performs best at 300+ DPI equivalent)
- Convert to grayscale
- Auto-detect dark UI and invert
- Enhance contrast
- Sharpen
- Run OCR with tuned config
"""

import logging
import os
import statistics

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
    logger.warning("pytesseract not available — OCR will return empty string")

# Minimum dimension before upscaling
MIN_DIMENSION = 1000
# Upscale factor applied when image is small
UPSCALE_FACTOR = 2.0
# Darkness threshold: mean pixel value below this = dark UI
DARK_UI_THRESHOLD = 100


def extract_text_from_image(image_path: str) -> str:
    """
    Accept a file path to a single image. Return extracted text string.

    This is the public interface called by api.py.
    Raises on unrecoverable error — api.py wraps this in try/except and
    returns a structured AnalysisResponse(status='error') to the caller.
    """
    if not TESSERACT_AVAILABLE:
        logger.warning("Tesseract unavailable — returning empty string")
        return ""

    image = Image.open(image_path)
    processed = _preprocess(image)

    config = "--psm 6 --oem 3"
    text = pytesseract.image_to_string(processed, config=config)

    logger.info("extract_text_from_image: %d chars extracted from %s", len(text), image_path)
    return text


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
        logger.debug("Upscaled image from %dx%d to %s", w, h, image.size)

    gray = image.convert("L")

    pixels = list(gray.getdata())
    mean_brightness = statistics.mean(pixels)
    logger.debug("Mean brightness: %.1f", mean_brightness)

    if mean_brightness < DARK_UI_THRESHOLD:
        gray = ImageOps.invert(gray)
        logger.debug("Dark UI detected — inverted image")

    enhancer = ImageEnhance.Contrast(gray)
    gray = enhancer.enhance(2.0)

    gray = gray.filter(ImageFilter.SHARPEN)

    return gray
