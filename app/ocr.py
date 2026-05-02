"""
ocr.py - VibeLenz image text extraction with preprocessing.

Extraction strategy (priority order):
1. Claude vision API — primary path for screenshot inputs.
   Handles dark mode, scattered bubble layouts, emoji, mixed fonts,
   and partial redaction in a single API call. Returns YOU/THEM attributed text.
2. Tesseract PSM 11 with layout-aware line grouping — fallback when vision unavailable.
3. Tesseract flat image_to_string — final fallback if layout grouping fails.

Fail-closed: any unrecoverable error re-raises to caller (main.py handles with 503).

VISION PATH (added)
-------------------
_extract_with_vision() sends the raw image bytes to Claude Haiku via the vision API.
The system prompt instructs it to extract text with YOU:/THEM: speaker attribution
based on bubble position. This replaces Tesseract as the primary path.

TESSERACT PATH (retained as fallback)
--------------------------------------
Preprocessing pipeline:
- Upscale small images (Tesseract performs best at 300+ DPI equivalent)
- Convert to grayscale
- Auto-detect dark UI and invert
- Erase solid-color redaction bars (replace with white) [F3]
- Enhance contrast
- Sharpen
- Run OCR with PSM 11 (sparse text) [F1]
- Word-confidence quality gate [F2]
"""

import base64
import io
import logging
import os
import statistics
from typing import List

import anthropic as _anthropic

logger = logging.getLogger("vibelenz.ocr")

try:
    import pytesseract
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps
    try:
        import numpy as np
        _NUMPY_AVAILABLE = True
    except ImportError:
        np = None  # type: ignore[assignment]
        _NUMPY_AVAILABLE = False

    if os.name == "nt":
        pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

    TESSERACT_AVAILABLE = True
    logger.info("pytesseract loaded successfully")
except ImportError:
    TESSERACT_AVAILABLE = False
    _NUMPY_AVAILABLE = False
    np = None  # type: ignore[assignment]
    logger.warning("pytesseract not available — vision API is primary, Tesseract fallback disabled")


# --- Tesseract preprocessing constants ---
MIN_DIMENSION = 1000
UPSCALE_FACTOR = 2.0
DARK_UI_THRESHOLD = 100
MEAN_CONF_THRESHOLD = 40
MIN_QUALITY_WORDS = 10
SOLID_BAND_VARIANCE = 5

# --- Vision model ---
_VISION_MODEL = "claude-haiku-4-5-20251001"
_VISION_MAX_TOKENS = 1500

_VISION_SYSTEM_PROMPT = """You are a text extraction assistant for a conversation safety analysis tool.

Extract all visible message text from this chat screenshot and return it with speaker attribution.

IMPORTANT — SPEAKER ATTRIBUTION RULE:
Bubble position is the ONLY authority for determining who said what.
- Right-aligned bubbles (green on iOS, darker color on Android) are ALWAYS labeled YOU:
- Left-aligned bubbles (gray or dark on iOS, lighter on Android) are ALWAYS labeled THEM:
Do NOT infer speaker from conversation content, tone, or who appears to be rejecting or pursuing.
Do NOT override bubble position based on what the message says.
If a message is on the right side, it is YOU: — no exceptions.

EXTRACTION RULES:
- Preserve message order top to bottom exactly as shown in the image.
- Copy text exactly as written — preserve typos, abbreviations, capitalization.
- Skip UI chrome: timestamps, "Delivered", "Read", app headers, notification banners.
- If a region is covered by a solid-color redaction bar, skip it entirely — do not guess.
- If a word is unclear, write [unclear] rather than inventing text.
- Return only the labeled messages, one message segment per line.

OUTPUT FORMAT (follow exactly):
THEM: first message text here
YOU: reply here
THEM: next message here

Return only the extracted messages. No preamble, no explanation, no markdown."""


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def extract_text_from_images(image_bytes_list: List[bytes]) -> str:
    """
    Accept list of raw image bytes. Return combined extracted text string.
    Raises RuntimeError on unrecoverable error (caller must handle).
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


# ---------------------------------------------------------------------------
# Vision extraction (primary)
# ---------------------------------------------------------------------------

def _media_type(image_bytes: bytes) -> str:
    if image_bytes[:4] == b"\x89PNG":
        return "image/png"
    if image_bytes[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    return "image/png"  # safe default


def _extract_with_vision(image_bytes: bytes, idx: int) -> str:
    """
    Extract conversation text using Claude's vision API.

    Handles dark mode, scattered bubble layouts, emoji, mixed font weights,
    and partial redaction in a single call. Returns YOU:/THEM: attributed text.
    Raises RuntimeError if API key is missing or the API call fails.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set — vision extraction unavailable")

    media_type = _media_type(image_bytes)
    image_data = base64.standard_b64encode(image_bytes).decode("utf-8")

    client = _anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=_VISION_MODEL,
        max_tokens=_VISION_MAX_TOKENS,
        system=_VISION_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_data,
                        },
                    },
                    {
                        "type": "text",
                        "text": "Extract all conversation messages from this screenshot with YOU:/THEM: labels.",
                    },
                ],
            }
        ],
    )

    text = message.content[0].text.strip()
    logger.info(f"Image {idx}: vision extraction returned {len(text)} chars")
    return text


# ---------------------------------------------------------------------------
# Tesseract extraction (fallback)
# ---------------------------------------------------------------------------

def _erase_solid_bands(gray: "Image.Image") -> "Image.Image":
    """
    Detect horizontal rows with near-zero pixel variance and replace with white.
    Removes redaction bars and solid UI bands that create false column boundaries.
    """
    if not _NUMPY_AVAILABLE or np is None:
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
    """Preprocess image for Tesseract: upscale, invert dark UI, erase bands, enhance contrast."""
    image = image.convert("RGB")

    w, h = image.size
    if min(w, h) < MIN_DIMENSION:
        image = image.resize((int(w * UPSCALE_FACTOR), int(h * UPSCALE_FACTOR)), Image.LANCZOS)
        logger.debug(f"Upscaled image from {w}x{h} to {image.size}")

    gray = image.convert("L")

    pixels = list(gray.getdata())
    mean_brightness = statistics.mean(pixels)
    if mean_brightness < DARK_UI_THRESHOLD:
        gray = ImageOps.invert(gray)
        logger.debug("Dark UI detected — inverted image")

    gray = _erase_solid_bands(gray)

    enhancer = ImageEnhance.Contrast(gray)
    gray = enhancer.enhance(2.0)
    gray = gray.filter(ImageFilter.SHARPEN)

    return gray


def _extract_with_tesseract(image_bytes: bytes, idx: int) -> str:
    """
    Tesseract-based extraction with layout-aware speaker attribution.
    PSM 11 (sparse text) — correct for chat bubble layouts.
    Falls back to flat image_to_string if layout grouping fails.
    Raises ValueError if word-confidence quality gate fires.
    """
    if not TESSERACT_AVAILABLE:
        raise RuntimeError(f"Image {idx}: Tesseract unavailable")

    image = Image.open(io.BytesIO(image_bytes))
    processed = _preprocess(image)
    img_width = processed.width
    config = "--psm 11 --oem 3"

    try:
        data = pytesseract.image_to_data(
            processed, config=config, output_type=pytesseract.Output.DICT
        )

        line_bucket_px = 15
        lines: dict = {}
        retained_confs: List[int] = []

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

            retained_confs.append(conf)
            bucket = (top // line_bucket_px) * line_bucket_px
            if bucket not in lines:
                lines[bucket] = {"words": [], "cx_sum": 0.0, "cx_count": 0}
            lines[bucket]["words"].append((left, word))
            lines[bucket]["cx_sum"] += center_x
            lines[bucket]["cx_count"] += 1

        mean_conf = statistics.mean(retained_confs) if retained_confs else 0
        word_count = len(retained_confs)
        logger.info(f"Image {idx}: Tesseract mean_conf={mean_conf:.1f}, word_count={word_count}")

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
        logger.info(f"Image {idx}: Tesseract layout extraction returned {len(text)} chars")
        return text

    except ValueError:
        raise  # let quality-gate ValueError propagate — main.py handles it
    except Exception as layout_err:
        logger.warning(f"Image {idx}: Tesseract layout failed ({layout_err}), falling back to flat OCR")
        text = pytesseract.image_to_string(processed, config=config)
        logger.info(f"Image {idx}: Tesseract flat fallback returned {len(text)} chars")
        return text


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def _extract_single(image_bytes: bytes, idx: int) -> str:
    """
    Extract text from a single image.

    Priority:
    1. Claude vision API (primary) — accurate on dark mode, chat layouts, emoji.
    2. Tesseract (fallback) — used when API key absent or vision call fails.

    Raises ValueError (quality gate) or RuntimeError (hard failure).
    """
    try:
        return _extract_with_vision(image_bytes, idx)
    except Exception as vision_err:
        logger.warning(f"Image {idx}: vision extraction failed ({vision_err}), falling back to Tesseract")
        return _extract_with_tesseract(image_bytes, idx)
