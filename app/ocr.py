"""
ocr.py - VibeLenz image text extraction.

Strategy:
1. PRIMARY: Claude vision API (Haiku) — handles dark mode, chat bubbles,
   redaction bars, emoji, and variable layouts natively. Requires ANTHROPIC_API_KEY.
2. FALLBACK: pytesseract with preprocessing — used if vision API is unavailable
   or returns an error. Lower accuracy on dark/complex screenshots.
3. Fail-closed: any unrecoverable error is re-raised to caller (main.py → 503/422).

ARCHITECTURE
------------
[V1] Vision-primary path (_extract_via_vision):
     Sends raw image bytes to claude-haiku-4-5-20251001 as base64 with a structured
     prompt. Model reads bubble position, color, and layout natively — no preprocessing
     needed. Returns YOU/THEM attributed text. Falls back to Tesseract on API error.

[V2] Tesseract fallback (_extract_via_tesseract):
     Original Tesseract pipeline retained as fallback. PSM 11 (sparse text), solid-band
     masking, and word-confidence quality gate from prior fixes are preserved.

[V3] Quality gate on Tesseract path only:
     Vision output is trusted. Quality gate (mean word confidence < 40 AND word count
     < 8) applies only to Tesseract fallback path, not vision path.
"""

import base64
import io
import logging
import os
import statistics
from typing import List, Optional

logger = logging.getLogger("vibelenz.ocr")

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False
    logger.warning("httpx not available — vision OCR path disabled")

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False

try:
    import pytesseract
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps

    if os.name == "nt":
        pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

    TESSERACT_AVAILABLE = True
    logger.info("pytesseract loaded successfully")
except ImportError:
    TESSERACT_AVAILABLE = False
    logger.warning("pytesseract not available — Tesseract fallback disabled")


# Vision API settings
VISION_MODEL = "claude-haiku-4-5-20251001"
VISION_TIMEOUT_S = 20.0
VISION_MAX_TOKENS = 1024

# Tesseract fallback settings
MIN_DIMENSION = 1000
UPSCALE_FACTOR = 2.0
DARK_UI_THRESHOLD = 100
MIN_WORD_CONFIDENCE = 40
MIN_WORD_COUNT = 8
SOLID_BAND_VARIANCE = 5.0

# Vision extraction prompt — instructs Claude to read bubble layout and attribute speakers.
# Constraints are tight to prevent the model from interpreting or editorializing.
_VISION_SYSTEM_PROMPT = (
    "You are a text extraction tool. Your only job is to read the messages visible in "
    "this chat screenshot and return them as plain labeled text. Do not interpret, "
    "analyze, summarize, or comment on the content."
)

_VISION_USER_PROMPT = (
    "Extract every visible message from this chat screenshot.\n\n"
    "Rules:\n"
    "- Label each message YOU or THEM based on bubble position: "
    "right-aligned or green bubbles = YOU, left-aligned or gray/dark bubbles = THEM.\n"
    "- Output one message per line in the format: YOU: <text> or THEM: <text>\n"
    "- Preserve the exact message text — do not paraphrase, correct, or summarize.\n"
    "- Skip timestamps, phone numbers, contact names, and UI elements.\n"
    "- If a message spans multiple lines, keep it on one output line.\n"
    "- Do not add any explanation, preamble, or commentary.\n\n"
    "Return only the labeled messages, nothing else."
)


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
    """
    Extract text from a single image.

    Tries vision API first (Claude Haiku). Falls back to Tesseract if:
    - ANTHROPIC_API_KEY is not set
    - httpx is unavailable
    - Vision API call fails for any reason
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    if api_key and HTTPX_AVAILABLE:
        try:
            text = _extract_via_vision(image_bytes, idx, api_key)
            if text and text.strip():
                logger.info(f"Image {idx}: vision OCR succeeded — {len(text)} chars")
                return text
            logger.warning(f"Image {idx}: vision OCR returned empty — falling back to Tesseract")
        except Exception as vision_err:
            logger.warning(f"Image {idx}: vision OCR failed ({vision_err}) — falling back to Tesseract")
    else:
        if not api_key:
            logger.info(f"Image {idx}: no API key — using Tesseract")
        if not HTTPX_AVAILABLE:
            logger.info(f"Image {idx}: httpx unavailable — using Tesseract")

    return _extract_via_tesseract(image_bytes, idx)


def _extract_via_vision(image_bytes: bytes, idx: int, api_key: str) -> str:
    """
    [V1] Extract conversation text using Claude vision (Haiku).

    Sends the raw image as base64 to the Anthropic messages API.
    The model reads bubble position and color to attribute YOU/THEM without
    any preprocessing — handles dark mode, redaction bars, and emoji natively.

    Returns speaker-attributed text in YOU:/THEM: format.
    Raises on API error or non-200 response so caller can fall back to Tesseract.
    """
    # Detect image media type from magic bytes
    if image_bytes[:8] == b'\x89PNG\r\n\x1a\n':
        media_type = "image/png"
    elif image_bytes[:2] == b'\xff\xd8':
        media_type = "image/jpeg"
    else:
        media_type = "image/jpeg"  # default — Anthropic accepts both

    b64_image = base64.standard_b64encode(image_bytes).decode("utf-8")

    payload = {
        "model": VISION_MODEL,
        "max_tokens": VISION_MAX_TOKENS,
        "system": _VISION_SYSTEM_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64_image,
                        },
                    },
                    {
                        "type": "text",
                        "text": _VISION_USER_PROMPT,
                    },
                ],
            }
        ],
    }

    response = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json=payload,
        timeout=VISION_TIMEOUT_S,
    )
    response.raise_for_status()

    data = response.json()
    raw = data["content"][0]["text"].strip()
    logger.debug(f"Image {idx}: vision raw output: {raw[:200]}")
    return raw


def _extract_via_tesseract(image_bytes: bytes, idx: int) -> str:
    """
    [V2] Tesseract fallback path.

    Preprocesses the image (upscale, grayscale, dark-mode inversion,
    solid-band masking, contrast enhance, sharpen) then runs pytesseract
    with PSM 11 (sparse text). Applies word-confidence quality gate [V3].
    """
    if not TESSERACT_AVAILABLE:
        logger.warning(f"Image {idx}: Tesseract unavailable — returning empty")
        return ""

    image = Image.open(io.BytesIO(image_bytes))
    processed = _preprocess(image)
    img_width = processed.width
    config = "--psm 11 --oem 3"

    try:
        data = pytesseract.image_to_data(
            processed, config=config, output_type=pytesseract.Output.DICT
        )

        # [V3] Word-confidence quality gate — Tesseract path only.
        retained_confs = [
            int(data["conf"][i])
            for i in range(len(data["text"]))
            if (data["text"][i] or "").strip() and int(data["conf"][i]) >= 20
        ]
        mean_conf = statistics.mean(retained_confs) if retained_confs else 0
        word_count = len(retained_confs)
        logger.info(f"Image {idx}: Tesseract mean_conf={mean_conf:.1f}, words={word_count}")

        if mean_conf < MIN_WORD_CONFIDENCE and word_count < MIN_WORD_COUNT:
            raise ValueError(
                f"OCR quality too low: mean_conf={mean_conf:.1f} < {MIN_WORD_CONFIDENCE}, "
                f"word_count={word_count} < {MIN_WORD_COUNT}."
            )

        line_bucket_px = 15
        lines: dict = {}
        for i in range(len(data["text"])):
            word = (data["text"][i] or "").strip()
            if not word:
                continue
            conf = int(data["conf"][i])
            if conf < 20:
                continue
            top = data["top"][i]
            left = data["left"][i]
            width = data["width"][i]
            center_x = left + width / 2
            bucket = (top // line_bucket_px) * line_bucket_px
            if bucket not in lines:
                lines[bucket] = {"words": [], "cx_sum": 0.0, "cx_count": 0}
            lines[bucket]["words"].append((left, word))
            lines[bucket]["cx_sum"] += center_x
            lines[bucket]["cx_count"] += 1

        if not lines:
            raise ValueError("No lines detected by image_to_data")

        result_parts: list = []
        prev_speaker = None

        for bucket in sorted(lines.keys()):
            line = lines[bucket]
            avg_cx = line["cx_sum"] / line["cx_count"]
            rel_x = avg_cx / img_width

            if rel_x > 0.52:
                speaker = "YOU"
            elif rel_x < 0.48:
                speaker = "THEM"
            else:
                speaker = None

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
        logger.info(f"Image {idx}: Tesseract extracted {len(text)} chars")
        return text

    except Exception as layout_err:
        logger.warning(f"Image {idx}: Tesseract layout failed ({layout_err}), trying flat OCR")
        text = pytesseract.image_to_string(processed, config=config)
        logger.info(f"Image {idx}: Tesseract flat fallback {len(text)} chars")
        return text


def _preprocess(image):
    """Preprocess for Tesseract fallback only. Vision path skips this."""
    image = image.convert("RGB")
    w, h = image.size
    if min(w, h) < MIN_DIMENSION:
        scale = UPSCALE_FACTOR
        image = image.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        logger.debug(f"Upscaled {w}x{h} -> {image.size}")

    gray = image.convert("L")
    pixels = list(gray.getdata())
    mean_brightness = statistics.mean(pixels)

    if mean_brightness < DARK_UI_THRESHOLD:
        gray = ImageOps.invert(gray)
        logger.debug("Dark UI detected — inverted")

    if NUMPY_AVAILABLE:
        try:
            arr = np.array(gray)
            row_variance = arr.var(axis=1)
            solid_rows = row_variance < SOLID_BAND_VARIANCE
            solid_count = int(solid_rows.sum())
            if solid_count > 0:
                arr[solid_rows] = 255
                gray = Image.fromarray(arr)
                logger.debug(f"Masked {solid_count} solid-color rows")
        except Exception as e:
            logger.warning(f"Solid-band masking failed: {e}")

    enhancer = ImageEnhance.Contrast(gray)
    gray = enhancer.enhance(2.0)
    gray = gray.filter(ImageFilter.SHARPEN)
    return gray
