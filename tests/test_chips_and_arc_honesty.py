# Copyright © 2026 Ricky Sessums. All rights reserved.
"""2026-11-08: fallback chips ("casual vibes only", "just vibes", "jury's still out") and an
uninformative flat arc ("Steady and easy") no longer render, because they contradicted the written
read on a heated debate. Real labels and informative arcs still render."""
from tests.test_render_v2 import _render_result, _visible_text

CONN = dict(presentation_mode="connection", lane="BENIGN", risk_level="LOW", confidence=0.6)
ROUTINE_TURNS = {"turn_count": 2, "arc": "flat_low", "direction": "neutral", "turns": [
    {"turn_number": 1, "color": "low", "label": "routine message"},
    {"turn_number": 2, "color": "low", "label": "routine message"}]}


def _text(**kw):
    return _visible_text(_render_result(**dict(CONN, **kw)))


def test_fallback_labels_render_nothing():
    t = _text(social_tone="fairly low-stakes", human_label="low-stakes interaction",
              interest_summary="context dependent")
    for s in ("casual vibes only", "just vibes", "jury's still out", "jury&#39;s still out"):
        assert s not in t, s
    assert 'class="gauge-tier"' not in _render_result(**dict(CONN, social_tone="fairly low-stakes",
                                                     human_label="low-stakes interaction",
                                                     interest_summary="context dependent"))


def test_real_labels_still_render():
    t = _text(social_tone="playful, flirtatious, and reciprocal", human_label="casual flirtation",
              interest_summary="good receptivity")
    assert "flirty and mutual" in t and "flirty, no rush" in t and "they're feeling it" in t.replace("&#39;", "'")


def test_one_real_label_renders_alone_without_separator():
    html = _render_result(**dict(CONN, human_label="warm receptivity", interest_summary="context dependent"))
    assert "warm energy" in html and "·" not in html.split('class="gauge-tier"', 1)[1].split("</div>", 1)[0]


def test_uninformative_flat_arc_hidden():
    t = _text(turn_analysis=ROUTINE_TURNS)
    assert "Steady and easy" not in t and "Conversation arc" not in t


def test_flat_arc_with_real_moods_kept():
    turns = dict(ROUTINE_TURNS, turns=[{"turn_number": 1, "color": "low", "label": "warm receptivity"},
                                       {"turn_number": 2, "color": "low", "label": "routine message"}])
    assert "Steady and easy" in _text(turn_analysis=turns)


def test_moving_arc_kept():
    turns = dict(ROUTINE_TURNS, arc="escalating", direction="worsening")
    assert "Conversation arc" in _text(turn_analysis=turns)
