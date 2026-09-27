# Copyright © 2026 Ricky Sessums. All rights reserved.
"""
Render v2 (Part A) regression tests.

Renders the real templates with the real AnalysisResponse field set and checks
the governance rules: surface chosen server-side and fail-closed to dark,
no numeric scores in visible output, connection copy free of scanner words,
and a static sample demo with no network calls.
"""
import os
import re

import jinja2
import pytest

from app.schemas import AnalysisResponse

TEMPLATES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")
ENV = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATES), autoescape=True)
BANNED_CONNECTION_WORDS = ["flag", "signal", "risk", "pattern", "danger", "pressure"]


def _payload(**overrides):
    ctx = AnalysisResponse().model_dump()
    ctx.update(
        request_id="req-test",
        risk_level="LOW",
        suggested_replies=[],
        replies_suppressed=False,
        analysis_mode="standard",
    )
    ctx.update(overrides)
    return ctx


def _render_result(**overrides):
    return ENV.get_template("result.html").render(**_payload(**overrides))


def _html_class(html):
    m = re.search(r'<html[^>]*class="([^"]+)"', html)
    assert m, "no class on <html>"
    return m.group(1).split()


def _visible_text(html):
    html = re.sub(r"<(script|style|details)\b.*?</\1>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", html)


CONNECTION = dict(presentation_mode="connection", lane="BENIGN", risk_level="LOW", confidence=0.82)


@pytest.mark.parametrize(
    "overrides, expected",
    [
        (CONNECTION, "vl-paper"),
        (dict(presentation_mode="risk", lane="FRAUD", risk_level="HIGH", risk_score=81), "vl-dark"),
        (dict(presentation_mode="risk", lane="COERCION_RISK", risk_level="HIGH"), "vl-dark"),
        (dict(presentation_mode="risk", lane="BENIGN", risk_level="LOW"), "vl-dark"),
        (dict(presentation_mode="risk", lane="BENIGN", risk_level="WITHHELD"), "vl-dark"),
        # Defense in depth: even if a connection presentation leaks through, risk lanes stay dark.
        (dict(CONNECTION, lane="FRAUD"), "vl-dark"),
        (dict(CONNECTION, lane="COERCION_RISK"), "vl-dark"),
        (dict(CONNECTION, risk_level="MEDIUM"), "vl-dark"),
        (dict(CONNECTION, degraded=True, degradation_reason="llm_unavailable"), "vl-dark"),
    ],
)
def test_result_surface_is_server_selected(overrides, expected):
    classes = _html_class(_render_result(**overrides))
    assert expected in classes
    assert ("vl-paper" in classes) != ("vl-dark" in classes)


def test_missing_lane_and_mode_fail_closed_to_dark():
    ctx = _payload()
    ctx.pop("presentation_mode")
    ctx.pop("lane")
    html = ENV.get_template("result.html").render(**ctx)
    assert "vl-dark" in _html_class(html)


def test_no_lane_toggle_in_result():
    html = _render_result(**CONNECTION)
    assert "data-lane" not in html and "lane-toggle" not in html


def test_risk_result_shows_band_not_numeric_score():
    html = _render_result(presentation_mode="risk", lane="FRAUD", risk_level="HIGH", risk_score=73, confidence=0.64)
    text = _visible_text(html)
    assert "High concern" in text
    assert not re.search(r"\b73\b", text)
    assert not re.search(r"\b64\s*%", text)


def test_connection_result_shows_band_not_numeric_score():
    html = _render_result(**dict(CONNECTION, risk_score=12, interest_score=88))
    text = _visible_text(html)
    assert "Clear read" in text
    assert not re.search(r"\b(88|82|12)\b", text)
    assert "VIBE SCORE" not in html and "RISK SCORE" not in html


def test_turn_dots_show_sequence_not_scores():
    turns = {
        "turn_count": 2,
        "arc_label": "Warming up",
        "direction": "improving",
        "turns": [
            {"turn_number": 1, "risk_score": 57, "color": "low", "verdict": "ok", "label": "open"},
            {"turn_number": 2, "risk_score": 43, "color": "low", "verdict": "ok", "label": "warm"},
        ],
    }
    text = _visible_text(_render_result(**dict(CONNECTION, turn_analysis=turns)))
    assert not re.search(r"\b(57|43)\b", text)


@pytest.mark.parametrize("label", ["casual flirtation", "genuine mixed signals", "mixed intent", "fear-driven urgency"])
def test_connection_copy_has_no_scanner_words(label):
    html = _render_result(
        **dict(
            CONNECTION,
            positive_signals=["mutual_teasing"],
            concern_signals=["slow_replies"],
            diagnosis="This one reads warm.",
            reasoning="They keep the thread going.",
            human_label=label,
            social_tone="playful, flirtatious, and reciprocal",
            interest_summary="high but pressured",
        )
    )
    m = re.search(r'<div class="vl-conn-region">(.*?)<!-- /vl-conn-region -->', html, flags=re.S)
    region = m.group(1) if m else ""
    assert region, "connection region marker missing"
    text = _visible_text(region).lower()
    for word in BANNED_CONNECTION_WORDS:
        assert word not in text, f"scanner word in connection copy: {word!r}"


def test_share_card_copy_is_mode_aware():
    html = _render_result(**CONNECTION)
    assert "WHAT'S WORKING" in html and "WORTH WATCHING" in html
    assert "score and flags only" not in html


@pytest.mark.parametrize("mode, expected", [("connection", "vl-paper"), ("risk", "vl-dark")])
def test_index_surface_follows_page_mode(mode, expected):
    html = ENV.get_template("index.html").render(page_mode=mode)
    assert expected in _html_class(html)


def test_index_has_no_false_privacy_claim_or_lane_toggle():
    html = ENV.get_template("index.html").render(page_mode="connection")
    assert "on-device" not in html.lower()
    assert "data-lane" not in html and "lane-toggle" not in html


def test_index_samples_are_static_and_labeled():
    html = ENV.get_template("index.html").render(page_mode="connection")
    assert html.count("Sample — not your conversation") == 2
    js = re.search(r'<script id="vl-sample-js">(.*?)</script>', html, flags=re.S)
    assert js, "sample script missing"
    body = js.group(1)
    for forbidden in ("fetch(", "XMLHttpRequest", "gtag(", "sendBeacon", "/analyze"):
        assert forbidden not in body, f"sample demo must not make requests: {forbidden}"
    warm = re.search(r'data-vl-panel="warm"(.*?)</section>', html, flags=re.S).group(1).lower()
    for word in BANNED_CONNECTION_WORDS:
        assert word not in _visible_text(warm), f"scanner word in warm sample: {word!r}"


def test_css_partial_is_jinja_safe_and_has_tokens():
    css = open(os.path.join(TEMPLATES, "_vl_render_v2.css"), encoding="utf-8").read()
    assert "{#" not in css and "{%" not in css and "{{" not in css
    for token in ("--vl-paper-bg: #F7F3EC", "--vl-risk-bg: #0A0E14", "--vl-mint-text: #047857"):
        assert token in css
    assert "prefers-reduced-motion: reduce" in css


RELATIONSHIP_ONLY_TYPES = {"dating", "family", "friend", "business", "partner"}  # analyzer_combined.py


def test_texting_picker_offers_relationships_and_keeps_fraud_screening():
    html = ENV.get_template("index.html").render(page_mode="connection")
    m = re.search(r'<select class="gl-sel" id="relType">(.*?)</select>', html, flags=re.S)
    assert m, "relationship picker missing"
    options = dict((v, label) for v, label in re.findall(r'<option value="([^"]+)">([^<]+)</option>', m.group(1)))
    for label in ("Dating app match", "Work associate", "Friend", "Family member", "Ex / Past partner", "Current partner"):
        assert label in options.values(), label
    # Every value must keep the fraud-aware prompt (none may be relationship-only).
    assert not (set(options) & RELATIONSHIP_ONLY_TYPES), set(options) & RELATIONSHIP_ONLY_TYPES
    assert "fd.append('relationship_type', document.getElementById('relType').value);" in html


def test_gender_picker_removed_but_field_still_sent():
    html = ENV.get_template("index.html").render(page_mode="connection")
    assert 'id="genderSel"' not in html
    assert "A woman" not in html and "A man" not in html
    assert '<input type="hidden" id="otherGender" name="other_gender" value="unknown"/>' in html


def test_purport_brand_removed():
    for name in ("index.html", "result.html"):
        src = open(os.path.join(TEMPLATES, name), encoding="utf-8").read()
        assert "PurPort" not in src and "/scam-check" not in src, name


def test_brand_subtitle_is_social_intelligence():
    html = ENV.get_template("index.html").render(page_mode="connection")
    assert '<div class="brand-sub">Social Intelligence</div>' in html
    assert "Dating Intelligence" not in html


def test_front_door_privacy_copy_is_truthful():
    # Training notice goes live with this copy; data collected before it is never used for training.
    html = ENV.get_template("index.html").render(page_mode="connection")
    for gone in ("Never used to train AI", "Never shared or sold", "expires automatically after 30 days", "never shared"):
        assert gone not in html, gone
    assert "help sharpen the lens" in html
    assert "read by our AI provider" in html


# ---------------------------------------------------------------- results copy (connection)
LOW_TURNS = {
    "turn_count": 3, "arc": "flat_low", "direction": "neutral",
    "arc_label": "Low and stable — nothing escalated across these screenshots",
    "turns": [
        {"turn_number": 1, "risk_score": 5, "color": "low", "verdict": "Low concern", "label": "routine message"},
        {"turn_number": 2, "risk_score": 8, "color": "low", "verdict": "Low concern", "label": "warm receptivity"},
        {"turn_number": 3, "risk_score": 4, "color": "low", "verdict": "Low concern", "label": "casual flirtation"},
    ],
}


def _visible_region(html):
    m = re.search(r'<div class="vl-conn-region">(.*?)<!-- /vl-conn-region -->', html, flags=re.S)
    assert m, "connection region marker missing"
    return _visible_text(m.group(1))


def test_connection_header_plain_title_no_request_id_or_badge():
    html = _render_result(**dict(CONNECTION, mode_title="Connection Analysis",
                                 mode_tagline="Warm read on chemistry, receptivity, emotional movement, and what to do next."))
    text = _visible_text(html)
    assert "Conversation Analysis" in text
    assert "Request ID" not in text
    assert "Warm read on chemistry" not in text
    assert "Connection Analysis" not in text
    assert 'class="badge' not in html


def test_risk_header_keeps_request_id_and_badge():
    html = _render_result(presentation_mode="risk", lane="FRAUD", risk_level="HIGH", mode_title="Risk Analysis")
    assert "Request ID" in _visible_text(html)
    assert 'class="badge high"' in html


def test_first_card_eyebrow_is_the_vibe():
    text = _visible_text(_render_result(**CONNECTION))
    assert "THE VIBE" in text and "Connection Analytics" not in text


def test_low_arc_uses_everyday_words_not_concern():
    text = _visible_region(_render_result(**dict(CONNECTION, turn_analysis=LOW_TURNS)))
    assert "concern" not in text.lower()
    assert "Steady and easy" in text
    for mood in ("Easygoing", "Warm", "Flirty"):
        assert mood in text


def test_medium_turn_still_flagged_in_plain_words():
    turns = dict(LOW_TURNS, turns=LOW_TURNS["turns"][:2] + [
        {"turn_number": 3, "risk_score": 40, "color": "medium", "verdict": "Worth watching", "label": "mixed intent"}])
    text = _visible_region(_render_result(**dict(CONNECTION, turn_analysis=turns)))
    assert "Worth a closer look" in text


def test_empty_dampener_cards_removed():
    for ctx in (CONNECTION, dict(presentation_mode="risk", lane="FRAUD", risk_level="HIGH")):
        text = _visible_text(_render_result(**ctx))
        assert "No dampeners surfaced" not in text
        assert "What kept this from reading worse" not in text


@pytest.mark.parametrize("ctx, shown", [
    (dict(CONNECTION, relationship_type="coworker", extracted_text="THEM: meeting moved to 3\nYOU: works"), False),
    (dict(CONNECTION, relationship_type="family_member", extracted_text="THEM: dinner sunday?\nYOU: yes"), False),
    (dict(CONNECTION, relationship_type="match", extracted_text="THEM: hey you\nYOU: hi"), True),
    (dict(CONNECTION, relationship_type="stranger", extracted_text="THEM: still selling the bike? what's the price"), True),
    (dict(presentation_mode="risk", lane="FRAUD", risk_level="HIGH", relationship_type="stranger", extracted_text="hello"), True),
])
def test_photo_check_only_for_match_purchase_or_scam(ctx, shown):
    assert ("Check their photos" in _render_result(**ctx)) is shown
