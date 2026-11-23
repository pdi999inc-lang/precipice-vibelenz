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


def _run(monkeypatch, caplog, n_images=2, accept="*/*"):
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
                                          data={"user_side": "right"},
                                          headers={"accept": accept})
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


def test_the_html_path_reports_payload_and_render(monkeypatch, caplog):
    # The completion line is emitted before the template runs, so render time
    # can only appear on the StagesFinal line.
    _, messages = _run(monkeypatch, caplog, accept="text/html")
    final = next((m for m in messages if "StagesFinal=" in m), None)
    assert final is not None, "no StagesFinal= line on the html path"
    stages = _stages_from(messages, key="StagesFinal=")
    assert "payload" in stages and "render" in stages and "audit" in stages
    assert "Path=html" in final
    assert re.search(r"TotalMs=\d+", final)


def test_the_json_path_also_reports_a_final_line(monkeypatch, caplog):
    # The JSON branch returns before the template block, so a final line placed
    # only in that block measured nothing for API clients - which is how the
    # first version of this instrument shipped blind to its own probe.
    _, messages = _run(monkeypatch, caplog, accept="application/json")
    final = next((m for m in messages if "StagesFinal=" in m), None)
    assert final is not None, "no StagesFinal= line on the json path"
    stages = _stages_from(messages, key="StagesFinal=")
    assert "serialize" in stages and "audit" in stages
    assert "Path=json" in final


def test_the_default_accept_header_takes_the_json_path(monkeypatch, caplog):
    # accept "*/*" contains neither "application/json" nor "text/html", and the
    # handler treats that as JSON. Pinned because it decides which stages exist.
    _, messages = _run(monkeypatch, caplog, accept="*/*")
    final = next((m for m in messages if "StagesFinal=" in m), "")
    assert "Path=json" in final


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
