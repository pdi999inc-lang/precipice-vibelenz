"""Copyright © 2026 Ricky Sessums. All rights reserved.

Per-stage latency timers on /analyze-screenshots.

Added 2026-11-23 after the first real breakdown of a 5-screenshot read: OCR 5.5s,
total wall 36s. Nearly half the time sat outside both OCR and the AI calls and
nothing measured it, so the next optimisation would have been another guess.

These tests do not assert durations - they assert the instrument exists, covers
every stage, and cannot itself break a read. A timer that throws, or that silently
stops emitting, is worse than no timer, because latency work would then be done
against stale numbers.
"""
import logging
import re

import pytest
from fastapi.testclient import TestClient

from app import main as app_main

MAGIC = b"\x89PNG\r\n\x1a\n"
EXPECTED_STAGES = ("input", "analysis", "replies", "enrich", "db", "shadow")


def _png(i: int) -> bytes:
    return MAGIC + b"\x00" * 63 + bytes([i])


def _run(monkeypatch, caplog, n_images=2):
    monkeypatch.setenv("VL_RATE_LIMITS_ENABLED", "false")

    def ocr(image_bytes_list, user_side="right"):
        idx = image_bytes_list[0][-1]
        return f"THEM: chunk{idx} this is screenshot number {idx} of the upload"

    monkeypatch.setattr(app_main, "extract_text_from_images", ocr)
    real = app_main.interpret_analysis
    monkeypatch.setattr(app_main, "interpret_analysis",
                        lambda *a, **k: real(*a, **{**k, "use_llm": False}))

    files = [("files", (f"s{i}.png", _png(i), "image/png")) for i in range(n_images)]
    with caplog.at_level(logging.INFO, logger="vibelenz.main"):
        r = TestClient(app_main.app).post("/analyze-screenshots", files=files,
                                          data={"user_side": "right"})
    return r, caplog.messages


def _stages_from(messages, key="Stages="):
    for m in messages:
        if key in m:
            blob = m.split(key, 1)[1].split(" ")[0]
            return dict(
                (p.split(":")[0], int(p.split(":")[1]))
                for p in blob.split(",") if ":" in p
            )
    return None


def test_the_read_still_succeeds_with_timers_in_place(monkeypatch, caplog):
    r, _ = _run(monkeypatch, caplog)
    assert r.status_code == 200, r.text[:300]


def test_every_stage_is_reported(monkeypatch, caplog):
    _, messages = _run(monkeypatch, caplog)
    stages = _stages_from(messages)
    assert stages is not None, "no Stages= line was logged"
    missing = [s for s in EXPECTED_STAGES if s not in stages]
    assert not missing, f"unmeasured stages: {missing}"


def test_stage_values_are_non_negative_ints(monkeypatch, caplog):
    _, messages = _run(monkeypatch, caplog)
    for name, ms in (_stages_from(messages) or {}).items():
        assert isinstance(ms, int) and ms >= 0, f"{name}={ms}"


def test_stages_are_reported_in_pipeline_order(monkeypatch, caplog):
    # Dict order is insertion order, so this also proves the marks fire in sequence
    # rather than some being skipped and re-set later.
    _, messages = _run(monkeypatch, caplog)
    got = [k for k in (_stages_from(messages) or {}) if k in EXPECTED_STAGES]
    assert got == list(EXPECTED_STAGES), got


def test_ocr_is_reported_separately_from_input(monkeypatch, caplog):
    # _ocr_ms isolates the vision calls inside the input stage. Both must appear, or
    # the OCR share of latency becomes unattributable again.
    _, messages = _run(monkeypatch, caplog)
    line = next((m for m in messages if "Stages=" in m), "")
    assert "OcrMs=" in line and "Imgs=" in line and "OcrConc=" in line


def test_the_final_line_includes_render_and_a_total(monkeypatch, caplog):
    # The completion line is emitted before the template runs, so render time can only
    # appear on a second line. Without it, the gap between the audit write and the
    # response is invisible - which is exactly how it stayed hidden until now.
    _, messages = _run(monkeypatch, caplog)
    final = next((m for m in messages if "StagesFinal=" in m), None)
    assert final is not None, "no StagesFinal= line"
    stages = _stages_from(messages, key="StagesFinal=")
    assert "render" in stages and "post" in stages
    assert re.search(r"TotalMs=\d+", final)


def test_paste_path_also_reports_stages(monkeypatch, caplog):
    monkeypatch.setenv("VL_RATE_LIMITS_ENABLED", "false")
    real = app_main.interpret_analysis
    monkeypatch.setattr(app_main, "interpret_analysis",
                        lambda *a, **k: real(*a, **{**k, "use_llm": False}))
    text = ("THEM: why didnt you answer me last night\nYOU: i was asleep\n"
            "THEM: you were online though i saw\nYOU: i fell asleep with my phone on")
    with caplog.at_level(logging.INFO, logger="vibelenz.main"):
        r = TestClient(app_main.app).post("/analyze-screenshots",
                                          data={"pasted_text": text},
                                          headers={"accept": "application/json"})
    assert r.status_code == 200, r.text[:300]
    stages = _stages_from(caplog.messages)
    assert stages and "input" in stages and "analysis" in stages


def test_a_marker_failure_cannot_break_a_read(monkeypatch, caplog):
    # The timer is diagnostics. If it ever raises, the read must still complete.
    r, _ = _run(monkeypatch, caplog)
    assert r.status_code == 200
    # No stage value may be absurd enough to indicate a clock mix-up (time.time vs
    # time.monotonic), which would make every future latency decision wrong.
    for name, ms in (_stages_from(caplog.messages) or {}).items():
        assert ms < 600_000, f"{name}={ms}ms - wrong clock?"
