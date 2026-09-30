"""Tests for app/healthy_patterns.py.
Copyright © 2026 Ricky Sessums. All rights reserved."""
import copy
import re
import time

import pytest

from app import healthy_patterns as hp
from app.healthy_patterns import build_healthy_patterns, detect
from app.subtle_patterns import build_subtle_patterns
from tests.eval.healthy_patterns_eval import CASES

WARM = """YOU: I passed my board exam!!
THEM: congratulations!! so proud of you
THEM: want to facetime tonight?
THEM: dinner friday at 7? my treat
THEM: I told my sister about you"""


def payload(text=WARM, **over):
    base = {"extracted_text": text, "presentation_mode": "connection", "lane": "RELATIONSHIP_NORMAL",
            "risk_level": "LOW", "risk_score": 10, "degraded": False, "user_side": "right",
            "flags": [], "concern_signals": [], "positive_signals": ["reciprocal_engagement"]}
    base.update(over)
    return base


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_eval_regression_floor(case):
    got = set(detect(case["text"], attribution_ok=case.get("user_side", "right") != "mix")["found"])
    assert case["expect"] <= got, f"missed {case['expect'] - got}"
    assert not (case["forbid"] & got), f"false positive {case['forbid'] & got}"


# ---- safety gates: positives never sit next to a live risk ----
@pytest.mark.parametrize("over", [
    {"lane": "FRAUD"}, {"lane": "COERCION_RISK"}, {"risk_score": 60}, {"risk_score": 95},
    {"risk_score": "garbage"}, {"concern_signals": ["money_request"]},
    {"flags": '["payment_before_verification"]'}, {"flags": ["lure_and_pivot"]},
    {"lane": "BLOCKED"}, {"risk_level": "WITHHELD"}, {"injection_blocked": True},
])
def test_gated_off(over):
    assert build_healthy_patterns(payload(**over)) is None


@pytest.mark.parametrize("bad", [None, "x", 3, [], {}])
def test_malformed(bad):
    assert build_healthy_patterns(bad) is None


def test_gate_applies_in_risk_mode_too():
    assert build_healthy_patterns(payload(presentation_mode="risk", lane="FRAUD")) is None


# ---- risk mode: clinical, verifiable-only, with caveat ----
def test_risk_mode_only_verifiable_and_caveated():
    out = build_healthy_patterns(payload(presentation_mode="risk", risk_level="MEDIUM", risk_score=40))
    assert out["heading"] == "What lowers concern"
    assert out["caveat"] == hp.RISK_CAVEAT
    assert out["chips"] == []
    assert set(out["keys"]) <= hp.RISK_ELIGIBLE
    assert "celebrates_you" not in out["keys"]


# ---- connection mode ----
def test_connection_patterns_and_upgraded_chips():
    out = build_healthy_patterns(payload())
    assert out["heading"] == "What's going right"
    assert {"offers_to_meet", "concrete_plans", "celebrates_you", "open_about_you"} <= set(out["keys"])
    chip = out["chips"][0]
    assert chip["key"] == "reciprocal_engagement"
    assert chip["title"] != "Reciprocal engagement" and chip["means"]  # glossary copy, not raw key


def test_chip_contradicted_by_concern_is_suppressed():
    ff = """THEM: when we get married I'll take you to Italy
THEM: I can see us by the lake
YOU: when can we meet?
THEM: soon baby"""
    p = payload(ff, positive_signals=["vision_building_present", "reciprocal_engagement"])
    p["subtle_patterns"] = build_subtle_patterns(p)
    assert "future_faking" in p["subtle_patterns"]["keys"]
    out = build_healthy_patterns(p)
    assert "vision_building_present" not in [c["key"] for c in out["chips"]]


def test_coexist_note_when_mirror_and_concern_both_fire():
    mixed = """YOU: it hurt that you didn't show up
THEM: you're overreacting
YOU: I felt ignored all night
THEM: that's fair, I get why that felt bad"""
    p = payload(mixed)
    p["subtle_patterns"] = build_subtle_patterns(p)
    out = build_healthy_patterns(p)
    assert "feelings_validated" in out["keys"] and out["coexist"]


def test_unlabeled_gives_no_patterns_but_keeps_chips():
    out = build_healthy_patterns(payload("dinner friday at 7?\ncongrats!!"))
    assert out["patterns"] == [] and out["chips"]


def test_nothing_at_all_is_none():
    assert build_healthy_patterns(payload("THEM: ok\nYOU: k", positive_signals=[])) is None


# ---- purity ----
def test_deterministic_and_non_mutating():
    p = payload()
    before = copy.deepcopy(p)
    assert build_healthy_patterns(p) == build_healthy_patterns(p)
    assert p == before


# ---- voice ----
def _all_strings(voice):
    out = [c[voice][f] for c in hp.COPY.values() if voice in c for f in ("title", "body")]
    if voice == hp.RISK:
        out.append(hp.RISK_CAVEAT)
    else:
        out.append(hp.COEXIST_NOTE)
    return out


def test_connection_copy_has_no_clinical_words():
    for s in _all_strings(hp.CONNECTION):
        for w in hp.CLINICAL_WORDS:
            assert not re.search(rf"\b{w}", s, re.I), f"{w!r} in: {s}"


@pytest.mark.parametrize("voice", [hp.CONNECTION, hp.RISK])
def test_positives_never_overpromise(voice):
    for s in _all_strings(voice):
        for w in hp.OVERPROMISE:
            assert w not in s.lower(), f"{w!r} in: {s}"


def test_copy_coverage():
    for key in hp.ORDER:
        assert hp.CONNECTION in hp.COPY[key]
    for key in hp.COPY:
        assert (hp.RISK in hp.COPY[key]) == (key in hp.RISK_ELIGIBLE)


def test_adversarial_input_is_fast():
    evil = ("THEM: " + "what do you " * 2000 + "\n") * 20
    t = time.time()
    build_healthy_patterns(payload(evil))
    assert time.time() - t < 2.0
