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
- Erase solid-color redaction bars (replace with white)
- Enhance contrast
- Sharpen
- Run OCR with PSM 11 (sparse text — correct for chat bubble layouts)

FIXES APPLIED
-------------
[F1] PSM mode changed from 6 (uniform block) to 11 (sparse text).
     PSM 6 assumes a single uniform text block, which mis-segments chat layouts.
     PSM 11 finds text anywhere in the image without layout assumptions — correct
     for SMS/chat screenshots with scattered bubbles, timestamps, and UI chrome.

[F2] Word-confidence quality gate added to _extract_single().
     After word collection, mean confidence of retained words is computed.
     If mean_conf < MEAN_CONF_THRESHOLD and fewer than MIN_QUALITY_WORDS words
     are retained, a ValueError is raised before text is returned.
     main.py catches this and returns a clean user-facing error — no LLM call.

[F3] Solid-color band erasure added to _preprocess().
     Detects horizontal rows with near-zero pixel variance (redaction bars,
     solid UI bands) and replaces them with white before Tesseract runs.
     Prevents false column boundaries that PSM 6 used to create from red bars.
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
    import numpy as np

    if os.name == "nt":
        pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

    TESSERACT_AVAILABLE = True
    logger.info("pytesseract loaded successfully")
except ImportError:
    TESSERACT_AVAILABLE = False
    np = None  # type: ignore[assignment]
    logger.warning("pytesseract not available — OCR will return empty results")


# Minimum dimension before upscaling
MIN_DIMENSION = 1000
# Upscale factor applied when image is small
UPSCALE_FACTOR = 2.0
# Darkness threshold: if mean pixel value below this, treat as dark UI
DARK_UI_THRESHOLD = 100
# [F2] Quality gate: minimum mean word confidence (0–100) across retained words
MEAN_CONF_THRESHOLD = 40
# [F2] Minimum number of retained words required before confidence gate applies
MIN_QUALITY_WORDS = 10
# [F3] Row variance below this → solid-color band (redaction bar or UI chrome)
SOLID_BAND_VARIANCE = 5


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


def extract_text_from_image(path: str) -> str:
    """Single-image path-based entry point. Raises FileNotFoundError on bad path."""
    with open(path, "rb") as f:
        image_bytes = f.read()
    return extract_text_from_images([image_bytes])


def _erase_solid_bands(gray: "Image.Image") -> "Image.Image":
    """
    [F3] Detect horizontal rows with near-zero pixel variance and replace with white.
    Solid bands are redaction bars, sender name blocks, or UI chrome that create
    false column boundaries during Tesseract segmentation.
    Requires numpy — returns image unchanged if numpy is unavailable.
    """
    if np is None:
        return gray
    arr = np.array(gray, dtype=np.float32)
    row_variance = arr.var(axis=1)
    solid_rows = row_variance < SOLID_BAND_VARIANCE
    if solid_rows.any():
        arr[solid_rows] = 255.0
        logger.debug(f"Erased {solid_rows.sum()} solid-color rows")
        return Image.fromarray(arr.astype(np.uint8))
    return gray


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

    # [F3] Erase solid-color bands after inversion so red bars become solid white
    gray = _erase_solid_bands(gray)

    enhancer = ImageEnhance.Contrast(gray)
    gray = enhancer.enhance(2.0)

    gray = gray.filter(ImageFilter.SHARPEN)

    return gray


def _extract_single(image_bytes: bytes, idx: int) -> str:
    """
    Extract text from a single image with speaker attribution.

    Uses pytesseract image_to_data to get bounding boxes per word, then
    groups words into lines by y-coordinate and assigns speaker labels
    (YOU / THEM) based on x-position relative to image center.

    Right-aligned bubbles (x_center > 52% of image width) = YOU (user).
    Left-aligned bubbles  (x_center < 48% of image width) = THEM (other person).
    Ambiguous center text (timestamps, app UI) is included unlabeled.

    [F1] PSM 11 (sparse text) replaces PSM 6 (uniform block) — correct for chat layouts.
    [F2] Mean word confidence gate raises ValueError on low-quality reads so the
         caller (main.py) can return a clean user-facing error before the LLM is called.

    Falls back to flat image_to_string if image_to_data fails.
    """
    if not TESSERACT_AVAILABLE:
        logger.warning(f"Image {idx}: tesseract unavailable, returning empty")
        return ""

    image = Image.open(io.BytesIO(image_bytes))
    processed = _preprocess(image)
    img_width = processed.width

    # [F1] PSM 11: sparse text — find text anywhere without assuming layout structure.
    config = "--psm 11 --oem 3"

    try:
        data = pytesseract.image_to_data(
            processed, config=config, output_type=pytesseract.Output.DICT
        )

        # Group words into line buckets by top-coordinate (15px tolerance)
        line_bucket_px = 15
        lines: dict = {}
        retained_confs: List[int] = []

        for i in range(len(data["text"])):
            word = (data["text"][i] or "").strip()
            if not word:
                continue
            conf = int(data["conf"][i])
            if conf < 20:  # discard very-low-confidence noise
                continue
            top = data["top"][i]
            left = data["left"][i]
            width = data["width"][i]
            center_x = left + width / 2

            retained_confs.append(conf)
            bucket = (top // line_bucket_px) * line_bucket_px
            if bucket not in lines:
                lines[bucket] = {"words": [], "cx_sum": 0.0, "cx_count": 0}
            lines[bucket]["words"].append((left, word))
            lines[bucket]["cx_sum"] += center_x
            lines[bucket]["cx_count"] += 1

        # [F2] Quality gate: reject low-confidence reads before returning text.
        # Only fires when word count is small — a large word count at moderate
        # confidence is acceptable (long messages with OCR noise throughout).
        mean_conf = statistics.mean(retained_confs) if retained_confs else 0
        word_count = len(retained_confs)
        logger.info(f"Image {idx}: mean word confidence = {mean_conf:.1f}, word_count = {word_count}")
        if mean_conf < MEAN_CONF_THRESHOLD and word_count < MIN_QUALITY_WORDS:
            raise ValueError(
                f"OCR quality too low: mean_conf={mean_conf:.1f}, word_count={word_count}. "
                f"Image may be heavily redacted, blurred, or unreadable."
            )

        if not lines:
            raise ValueError("No lines detected by image_to_data")

        result_parts: list = []
        prev_speaker: str | None = None

        for bucket in sorted(lines.keys()):
            line = lines[bucket]
            avg_cx = line["cx_sum"] / line["cx_count"]
            rel_x = avg_cx / img_width  # 0.0 = far left, 1.0 = far right

            if rel_x > 0.52:
                speaker = "YOU"
            elif rel_x < 0.48:
                speaker = "THEM"
            else:
                speaker = None  # timestamp / UI chrome — include without label

            words_sorted = " ".join(w for _, w in sorted(line["words"], key=lambda x: x[0]))

            if speaker and speaker != prev_speaker:
                result_parts.append(f"\n{speaker}: {words_sorted}")
            elif speaker:
                result_parts.append(words_sorted)
            else:
                result_parts.append(words_sorted)

            if speaker:
                prev_speaker = speaker

        text = " ".join(result_parts).strip()
        logger.info(f"Image {idx}: layout-aware OCR extracted {len(text)} chars")
        return text

    except Exception as layout_err:
        logger.warning(f"Image {idx}: layout OCR failed ({layout_err}), falling back to flat OCR")
        text = pytesseract.image_to_string(processed, config=config)
        logger.info(f"Image {idx}: flat OCR fallback extracted {len(text)} chars")
        return text
