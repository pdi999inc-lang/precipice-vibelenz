"""
conftest.py — VibeLenz test suite path and fixture setup.

Run from the project root:
    pytest tests/ -v

Requirements:
    pip install pytest requests pillow
"""
from __future__ import annotations

import io
import os
import sys

import pytest

# Add project root to sys.path so `from app.analyzer_combined import ...` resolves.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _make_png_with_text(text: str, width: int = 600, height: int = 200) -> bytes:
    """
    Generate a valid PNG image containing the given text on a white background.
    Requires Pillow — already a project dependency via ocr.py.
    Returns raw PNG bytes.
    """
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 16)
    except Exception:
        font = ImageFont.load_default()

    # Wrap text into lines so it fits the image width.
    words = text.split()
    lines, current = [], ""
    for word in words:
        test_line = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), test_line, font=font)
        if bbox[2] > width - 20 and current:
            lines.append(current)
            current = word
        else:
            current = test_line
    if current:
        lines.append(current)

    y = 10
    for line in lines:
        draw.text((10, y), line, fill=(0, 0, 0), font=font)
        y += 22

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(scope="session")
def sample_png_housing_fraud() -> bytes:
    """PNG image whose OCR text contains a housing fraud conversation cluster."""
    text = (
        "Hi I am the property manager. Your application is approved. "
        "Once your application is approved the showing can be scheduled. "
        "The owner contact information will be provided once you are interested in renting. "
        "Please send the deposit first and then we will arrange key pickup. "
        "The owner is currently out of town for work. Manny will handle the keys."
    )
    return _make_png_with_text(text)


@pytest.fixture(scope="session")
def sample_png_benign_dating() -> bytes:
    """PNG image with a benign reciprocal dating conversation."""
    text = (
        "Hey! How are you doing? I missed you haha. "
        "Me too! I was just thinking about you lol. "
        "That's so cool, we should totally hang out. "
        "Yeah definitely, let's make it happen. I can't wait!"
    )
    return _make_png_with_text(text)


@pytest.fixture(scope="session")
def sample_png_blank() -> bytes:
    """PNG image with almost no readable text — triggers MIN_OCR_CHARS guard."""
    return _make_png_with_text("   ", width=50, height=50)


@pytest.fixture(scope="session")
def live_base_url() -> str:
    """Railway deployment URL. Override with VIBELENZ_URL env var if needed."""
    return os.environ.get("VIBELENZ_URL", "https://app.appvibelenz.com")
