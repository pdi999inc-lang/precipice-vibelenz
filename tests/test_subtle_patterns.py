"""Tests for app/subtle_patterns.py.
Copyright © 2026 Ricky Sessums. All rights reserved."""
import copy
import re
import time

import pytest

from app import subtle_patterns as sp
from app.subtle_patterns import build_subtle_patterns, detect
from tests.eval.subtle_patterns_eval import CASES

SCAM = """THEM: I'm an engineer on an oil rig
THEM: add me on whatsapp, I rarely check this app
YOU: can we video call first?
THEM: can't video call, company does not allow cameras on the rig
THEM: keep this between us ok"""

MINIMIZE = """YOU: it hurt that you didn't show up
THEM: you're overreacting
YOU: sorry, maybe I'm just overthinking
YOU: never mind"""


def payload(text, **over):
    base = {
        "extracted_text": text, "presentation_mode": "connection", "lane": "RELATIONSHIP_NORMAL",
        "risk_level": "LOW", "risk_score": 10, "degraded": False, "user_side": "right",
        "flags": [], "concern_signals": [],
    }
    base.update(over)
    return base


# ---- eval set is a regression floor ----
@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_eval_regression_floor(case):
    got = set(detect(case["text"], attribution_ok=case.get("user_side", "right") != "mix")["found"])
    assert case["expect"] <= got, f"missed {case['expect'] - got}"
    assert not (case["forbid"] & got), f"false positive {case['forbid'] & got}"


# ---- determinism and purity ----
def test_deterministic():
    p = payload(SCAM)
    assert build_subtle_patterns(p) == build_subtle_patterns(p)


def test_does_not_mutate_payload_or_touch_lane():
    p = payload(SCAM, lane="RELATIONSHIP_NORMAL", risk_score=10, flags=["x"])
    before = copy.deepcopy(p)
    build_subtle_patterns(p)
    assert p == before


# ---- ineligibility returns absent state ----
@pytest.mark.parametrize("over", [
    {"lane": "BLOCKED"}, {"injection_blocked": True}, {"risk_level": "WITHHELD"},
    {"extracted_text": ""}, {"extracted_text": None}, {"extracted_text": 123},
])
def test_ineligible_is_none(over):
    assert build_subtle_patterns(payload(SCAM, **over)) is None


@pytest.mark.parametrize("bad", [None, "str", 5, [], {}])
def test_malformed_payload_is_none(bad):
    assert build_subtle_patterns(bad) is None


def test_clean_conversation_is_none():
    assert build_subtle_patterns(payload("THEM: dinner friday at 7?\nYOU: yes!")) is None


# ---- degraded mode still runs (this is the backstop's whole purpose) ----
def test_runs_when_degraded():
    out = build_subtle_patterns(payload(SCAM, degraded=True))
    assert out and out["safety_present"]


# ---- attribution fail-closed ----
def test_mix_user_side_skips_direction_dependent():
    out = build_subtle_patterns(payload(MINIMIZE, user_side="mix"))
    keys = set(out["keys"]) if out else set()
    assert "concern_minimized" not in keys and "user_rationalizing" not in keys


def test_unlabeled_text_sets_note_and_unknown_speaker():
    txt = "add me on whatsapp, I rarely check this app\nkeep this between us"
    out = build_subtle_patterns(payload(txt))
    assert out["labeled"] is False and out["note"]
    assert all(p["speaker"] == "unknown" for p in out["patterns"])


def test_user_side_patterns_attribute_to_you():
    out = build_subtle_patterns(payload(MINIMIZE))
    by = {p["key"]: p for p in out["patterns"]}
    assert by["user_rationalizing"]["speaker"] == "you"
    assert by["concern_minimized"]["speaker"] == "them"


# ---- combos and corroboration ----
def test_combos():
    assert "unverified_and_off_platform" in [c["key"] for c in build_subtle_patterns(payload(SCAM))["combos"]]
    assert "dismissed_then_retracted" in [c["key"] for c in build_subtle_patterns(payload(MINIMIZE))["combos"]]


def test_llm_corroboration_including_json_string_flags():
    out = build_subtle_patterns(payload(SCAM, flags='["platform_migration_early", "verification avoidance"]'))
    by = {p["key"]: p for p in out["patterns"]}
    assert by["platform_migration_push"]["corroborated_by_llm"] is True
    assert by["verification_dodging"]["corroborated_by_llm"] is True
    assert by["secrecy_request"]["corroborated_by_llm"] is False


# ---- voice rule ----
def test_connection_copy_has_no_clinical_words():
    strings = []
    for key in sp.COPY:
        strings += list(sp.COPY[key][sp.CONNECTION].values())
    strings += [c[sp.CONNECTION] for c in sp.COMBO_COPY.values()]
    strings.append(sp.NOTE_UNLABELED[sp.CONNECTION])
    for s in strings:
        for w in sp.CLINICAL_WORDS:
            assert not re.search(rf"\b{w}", s, re.I), f"clinical word {w!r} in connection copy: {s}"


def test_voice_follows_presentation_mode():
    assert build_subtle_patterns(payload(SCAM))["voice"] == "connection"
    assert build_subtle_patterns(payload(SCAM, presentation_mode="risk"))["voice"] == "risk"


def test_every_key_has_both_voices_and_tier():
    for key in sp.ORDER:
        assert set(sp.COPY[key]) == {sp.CONNECTION, sp.RISK}
        assert key in sp.TIER and key in sp.LLM_EQUIVALENTS


# ---- hostile input ----
def test_evidence_is_truncated():
    long = "THEM: add me on whatsapp " + "x" * 5000
    out = build_subtle_patterns(payload(long))
    assert len(out["patterns"][0]["evidence"]) <= sp.MAX_QUOTE


def test_huge_and_adversarial_input_is_fast():
    evil = ("THEM: " + "you're " * 3000 + "\n") * 20 + "YOU: " + "a " * 50000
    t = time.time()
    build_subtle_patterns(payload(evil))
    assert time.time() - t < 2.0


def test_html_is_not_interpreted_here():
    out = build_subtle_patterns(payload("THEM: <script>x</script> add me on whatsapp"))
    assert "<script>" in out["patterns"][0]["evidence"]  # escaping is Jinja's job; content preserved verbatim
