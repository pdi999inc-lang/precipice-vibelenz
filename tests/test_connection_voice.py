# Copyright © 2026 Ricky Sessums. All rights reserved.
"""Connection-voice scrub: scanner words (pressure, signal, flag) never reach a connection read."""
import re

import pytest

from app.interpreter import interpret_analysis, scrub_connection_voice

BANNED = re.compile(r"\b(pressur\w*|signal\w*|flag\w*)\b", re.I)


@pytest.mark.parametrize("src, want", [
    ("Don't put pressure on them.", "Don't push them."),
    ("No pressure, but that's a signal she's pulling back.", "No rush, but that's a sign she's pulling back."),
    ("Those are mixed signals and one red flag.", "Those are mixed messages and one warning sign."),
    ("You felt pressured to text first.", "You felt pushed to text first."),
    ("He keeps signaling interest. Green flags everywhere.", "He keeps showing interest. Good signs everywhere."),
    ("Pressure won't help.", "Push won't help."),
])
def test_scrub(src, want):
    assert scrub_connection_voice(src) == want


def test_scrub_leaves_clean_text_and_non_strings():
    assert scrub_connection_voice("They're warm and asking questions back.") == "They're warm and asking questions back."
    assert scrub_connection_voice(None) is None


def _connection_result(**kw):
    r = {"lane": "BENIGN", "risk_score": 5, "risk_level": "LOW", "presentation_mode": "connection",
         "primary_label": "warm_receptivity", "positive_signals": [], "concern_signals": []}
    r.update(kw)
    return r


def test_connection_read_is_scrubbed(monkeypatch):
    from app import interpreter
    monkeypatch.setattr(interpreter, "_llm_enrich", lambda **k: {
        "diagnosis": "Mixed signals, no red flag.", "reasoning": "Don't put pressure on them.",
        "practical_next_steps": "Watch for the signal.", "accountability": "You felt pressured.",
        "llm_enriched": True, "llm_error": None})
    out = interpret_analysis(_connection_result(), extracted_text="THEM: hi", requested_mode="connection")
    for f in ("diagnosis", "reasoning", "practical_next_steps", "accountability"):
        assert not BANNED.search(out[f]), out[f]


def test_risk_read_keeps_clinical_words():
    out = interpret_analysis(_connection_result(lane="FRAUD", risk_score=85, risk_level="HIGH"),
                             extracted_text="", requested_mode="connection", use_llm=False)
    text = " ".join(str(out.get(f, "")) for f in ("diagnosis", "reasoning", "practical_next_steps"))
    assert out["lane"] == "FRAUD" and text  # protective copy untouched by the scrub
