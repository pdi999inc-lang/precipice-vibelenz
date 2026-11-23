"""Copyright © 2026 Ricky Sessums. All rights reserved.

Per-image vision OCR runs concurrently (2026-11-22). Before the change the handler
looped over the uploads and awaited each vision call in turn, so a 5-screenshot read
made 5 serial calls: measured ~30s for 1 image and ~52s for 3.

The two properties that a future refactor would break, and that this file pins:

  1. ORDER. Chunks are assembled by upload index, never by completion order.
     screenshot_merge.merge_chunks removes repeated boundary bubbles between
     ADJACENT screenshots, so a shuffled transcript is a corrupted transcript. The
     test makes later images finish FIRST, which fails if anything sorts by arrival.

  2. FAIL-CLOSED. One unreadable image still fails the whole read. A partial
     transcript must never reach the analyser, because the missing screenshot is
     exactly where the pattern might be.

Rate limits are disabled per-test, which is the existing convention in
tests/test_rate_limit.py. That applies to the test client only; live probes never
disable them.
"""
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app import main as app_main

MAGIC = b"\x89PNG\r\n\x1a\n"
N = 5


def _png(idx: int) -> bytes:
    """A valid-magic PNG whose last byte carries its upload index."""
    return MAGIC + b"\x00" * 63 + bytes([idx])


def _files(n: int = N):
    return [("files", (f"shot{i}.png", _png(i), "image/png")) for i in range(n)]


class _Tracker:
    """Records call order, completion order, and peak concurrency."""

    def __init__(self):
        self.lock = threading.Lock()
        self.live = 0
        self.peak = 0
        self.completed = []

    def ocr(self, image_bytes_list, user_side="right"):
        idx = image_bytes_list[0][-1]
        with self.lock:
            self.live += 1
            self.peak = max(self.peak, self.live)
        # Later images finish FIRST. If the handler assembled by completion order,
        # the transcript would come back reversed and the order assertion would fail.
        time.sleep(0.05 * (N - idx))
        with self.lock:
            self.live -= 1
            self.completed.append(idx)
        return f"THEM: chunk{idx} this is screenshot number {idx} of the upload"


def _run(monkeypatch, tracker, n=N):
    """Post n screenshots, capture what reached the analyser, then stop the pipeline."""
    monkeypatch.setenv("VL_RATE_LIMITS_ENABLED", "false")
    monkeypatch.setattr(app_main, "extract_text_from_images", tracker.ocr)

    seen = {}

    def capture_then_stop(text, *a, **k):
        seen["text"] = text
        raise RuntimeError("stop after OCR")  # handler turns this into a handled error

    monkeypatch.setattr(app_main, "analyze_text", capture_then_stop)
    TestClient(app_main.app).post("/analyze-screenshots", files=_files(n),
                                  data={"user_side": "right"})
    return seen


# ------------------------------------------------------------------ order (property 1)

def test_chunks_are_assembled_by_upload_index_not_by_completion(monkeypatch):
    t = _Tracker()
    seen = _run(monkeypatch, t)
    assert "text" in seen, (
        "analyze_text was never reached - if the transcript fell under "
        "main.MIN_OCR_CHARS the chunk markers need to be longer, not the gate lower")
    assert len(seen["text"]) > app_main.MIN_OCR_CHARS
    text = seen["text"]

    positions = [text.index(f"chunk{i}") for i in range(N)]
    assert positions == sorted(positions), (
        f"transcript out of upload order: {positions}")

    # Guard the guard: if completion order happened to match upload order the test
    # above would pass for the wrong reason.
    assert t.completed != list(range(N)), (
        "images completed in upload order, so this test proved nothing")


# ------------------------------------------------------------ concurrency (the speed-up)

def test_images_actually_run_concurrently(monkeypatch):
    monkeypatch.setenv("VL_OCR_CONCURRENCY", "4")
    t = _Tracker()
    _run(monkeypatch, t)
    assert t.peak > 1, "OCR still serial - the speed-up is not in effect"
    assert t.peak <= 4, f"exceeded the configured bound: peak={t.peak}"


def test_concurrency_one_restores_serial_behaviour(monkeypatch):
    monkeypatch.setenv("VL_OCR_CONCURRENCY", "1")
    t = _Tracker()
    _run(monkeypatch, t)
    assert t.peak == 1, f"bound of 1 not honoured: peak={t.peak}"


# -------------------------------------------------------------- fail-closed (property 2)

def test_one_unreadable_image_fails_the_whole_read(monkeypatch):
    monkeypatch.setenv("VL_RATE_LIMITS_ENABLED", "false")

    def ocr(image_bytes_list, user_side="right"):
        idx = image_bytes_list[0][-1]
        if idx == 2:
            raise RuntimeError(f"OCR failure on image {idx}")
        return f"THEM: chunk{idx} this is screenshot number {idx} of the upload"

    monkeypatch.setattr(app_main, "extract_text_from_images", ocr)

    reached = []
    monkeypatch.setattr(app_main, "analyze_text",
                        lambda *a, **k: reached.append(True))

    r = TestClient(app_main.app).post("/analyze-screenshots", files=_files(),
                                      data={"user_side": "right"})
    assert not reached, "a partial transcript reached the analyser"
    assert r.status_code != 200 or "chunk" not in r.text


# ------------------------------------------------------------------- the bound itself

@pytest.mark.parametrize("raw,expected", [
    (None, 4), ("", 4), ("   ", 4),
    ("1", 1), ("4", 4), ("8", 8),
    ("0", 1), ("-3", 1),          # clamp up: a bound of 0 would deadlock
    ("99", 8),                     # clamp down: no unbounded burst at the API
    ("banana", 4), ("3.5", 4),     # unparseable -> default, never a crash
])
def test_ocr_concurrency_is_clamped(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("VL_OCR_CONCURRENCY", raising=False)
    else:
        monkeypatch.setenv("VL_OCR_CONCURRENCY", raw)
    assert app_main._ocr_concurrency() == expected
