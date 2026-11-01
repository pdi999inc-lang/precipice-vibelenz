# Copyright © 2026 Ricky Sessums. All rights reserved.
"""MEDIUM reads outside FRAUD/COERCION get copy that matches the gauge (option B, 2026-11-01)."""
import pytest

from app.interpreter import interpret_analysis, _medium_nonfraud_kind

OLD = "This does not currently read like a strong risk pattern."


def _r(**kw):
    r = {"lane": "BENIGN", "risk_score": 45, "risk_level": "MEDIUM", "primary_label": "x"}
    r.update(kw)
    return r


@pytest.mark.parametrize("text, kind", [
    ("U got snap or insta?\nText?? My fiancé has my snap pw so text is safer", "deception"),
    ("just wondering, why does your profile say 25 if you're 34?", "deception"),
    ("Your other photo wasn't even you\nThat was my brother", "deception"),
    ("You are gross with all that ink", "contempt"),
    ("Just quit acting like a brat and lets do a date", "contempt"),
    ("Please like people within your means\nF off", "contempt"),
    ("Drive u to your apartment\nwhat do I get out of it yk\nspoiled tf did u expect", "transactional"),
    ("do you think you'll get rid of your cat for me?", "general"),
])
def test_kind(text, kind):
    assert _medium_nonfraud_kind(text) == kind


@pytest.mark.parametrize("mode", ["connection", "risk"])
def test_medium_benign_read_no_longer_says_no_concern(mode):
    out = interpret_analysis(_r(), extracted_text="You are gross with all that ink",
                             requested_mode=mode, use_llm=False)
    assert out["presentation_mode"] == "risk"
    assert out["diagnosis"] != OLD and out["medium_read_kind"] == "contempt"
    for f in ("diagnosis", "reasoning", "practical_next_steps", "accountability"):
        assert out[f].strip()
    # never framed as a scam
    assert "scam" not in out["diagnosis"].lower() or "isn't" in out["diagnosis"].lower()


def test_low_risk_mode_keeps_old_copy():
    out = interpret_analysis(_r(risk_score=10, risk_level="LOW"), extracted_text="hi",
                             requested_mode="risk", use_llm=False)
    assert out["diagnosis"] == OLD and "medium_read_kind" not in out


def test_fraud_and_coercion_copy_unchanged():
    f = interpret_analysis(_r(lane="FRAUD", risk_score=80, risk_level="HIGH"), extracted_text="gross",
                           requested_mode="connection", use_llm=False)
    c = interpret_analysis(_r(lane="COERCION_RISK", risk_score=65, risk_level="MEDIUM"), extracted_text="gross",
                           requested_mode="connection", use_llm=False)
    assert "medium_read_kind" not in f and "medium_read_kind" not in c
    assert "verify independently" in f["practical_next_steps"]


def test_physical_harm_copy_wins():
    out = interpret_analysis(_r(lane="COERCION_RISK", risk_level="HIGH", primary_label="physical_harm_disclosed"),
                             extracted_text="you are gross", requested_mode="connection", use_llm=False)
    assert "911" in out["practical_next_steps"]
