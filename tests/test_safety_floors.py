# Copyright © 2026 Ricky Sessums. All rights reserved.
"""
Tests for app/safety_floors.py (raise-only deterministic floors + lane/score coherence).

Covers: every floor fires on its target, the user's own lines never trigger,
hard negatives stay quiet (incl. family/friend borrowing), raise-only invariant,
BLOCKED / physical-harm reads untouched, coherence rule, evidence scoring is
never NONE on a floor read, and the eval corpus (risk cases caught, normal
cases untouched).
"""
import json
import os

import pytest

from app.safety_floors import apply_safety_floors, detect, other_party_text
from app.analyzer_combined import analyze_text

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _r(**kw):
    base = {"lane": "BENIGN", "risk_score": 0, "risk_level": "LOW", "flags": ["No signals detected"],
            "key_signals": [], "positive_signals": ["reciprocal_engagement"], "key_dampeners": ["no_pressure"],
            "alternative_explanations": ["ordinary conversation"], "vie_action": "NONE"}
    base.update(kw)
    return base


# ---------------- each floor fires ----------------

@pytest.mark.parametrize("text, floor", [
    ("THEM: Send the codes and I'll confirm the keys are ready", "money_request_floor"),
    ("THEM: Would you be able to help with that? Just 800. I'll pay you back the second the transfer clears", "money_request_floor"),
    ("THEM: After the holding fee. 800 to reserve it.", "money_request_floor"),
    ("THEM: I'm sending a 6 digit code to your phone, read it back to me", "money_request_floor"),
    ("THEM: We changed banks. Please send this month's payment to the new account", "money_request_floor"),
    ("THEM: The first one is only 50 deposit. You make 80 back.", "money_request_floor"),
    ("THEM: There's a platform with daily signals. Most people start with 300-500.", "investment_pitch_floor"),
    ("THEM: Want me to walk you through a tiny starting deposit? Most people start with a couple hundred on the platform", "investment_pitch_floor"),
    ("THEM: Private investment group, I've doubled what I put in. You need a starting balance on their platform", "investment_pitch_floor"),
    ("THEM: You just accept the delivery and send it to the next address", "money_mule_floor"),
    ("THEM: I have screenshots of everything. Don't make me use them.", "leverage_threat_floor"),
    ("THEM: I already have some pictures of people who said no. They change their mind when they realize what happens if they don't.", "leverage_threat_floor"),
    ("THEM: Other people complicate things. They don't know what we have.\nTHEM: Can we keep this between us a little longer?", "isolation_secrecy_floor"),
])
def test_floor_fires(text, floor):
    assert floor in detect(text, "match")


# ---------------- user's own lines never trigger ----------------

def test_user_lines_ignored_when_labeled():
    t = "YOU: can you send me 500 for the deposit?\nTHEM: sure, sending now"
    assert detect(t, "match") == []
    assert "send me 500" not in other_party_text(t)


def test_unlabeled_text_fails_closed():
    # no YOU/THEM labels: attribution unknown, every line counts
    assert "money_request_floor" in detect("can you help with that? only 800, I'll pay you back when the transfer clears", "match")
    assert other_party_text("LEFT: a\nRIGHT: b") == "LEFT: a\nRIGHT: b"


# ---------------- hard negatives stay quiet ----------------

@pytest.mark.parametrize("rel, text", [
    ("friend", "THEM: hey can you lend me 50 till friday? car payment hit early\nTHEM: I'll pay you back friday"),
    ("family", "THEM: Mom can you send me 200 for books? I'll pay you back when my check comes"),
    ("dating", "THEM: can you cover dinner tonight? I'll get the next one\nTHEM: it was like 60 bucks lol"),
    ("dating", "THEM: can you send me your half of the airbnb? it's 340"),
    ("dating", "THEM: can you venmo me 25 for the concert tickets?"),
    ("match", "THEM: I work in finance, mostly trading. markets are wild\nTHEM: anyway want to get tacos saturday?"),
    ("match", "THEM: I've been doing crypto for 2 years, made some money, lost some. you into that?\nTHEM: dinner thursday?"),
    ("dating", "THEM: my sister is super judgy, don't tell her I'm 35 lol"),
    ("dating", "THEM: let's keep this between us for now, I want to surprise my parents at thanksgiving"),
    ("dating", "THEM: I took some pics at the lake today, want to see? everyone at work will be jealous"),
    ("friend", "THEM: we changed the restaurant, new address is 4th and main"),
    ("business", "THEM: invoice attached, same account as always. thanks!"),
])
def test_hard_negatives_quiet(rel, text):
    assert detect(text, rel) == []


def test_family_fires_only_with_scam_tell():
    assert detect("THEM: Mom can you send me 200? I'll pay you back", "family") == []
    assert "money_request_floor" in detect("THEM: Mom it's me, new number. Can you send 200 in gift cards? Send the codes", "family")


# ---------------- apply: raise-only, lanes, evidence ----------------

def test_floor_raises_lane_and_score_and_clears_reassurance():
    out = apply_safety_floors(_r(), "THEM: Send the codes and I'll confirm the keys are ready", "stranger")
    assert out["lane"] == "FRAUD" and out["risk_score"] >= 60 and out["risk_level"] == "MEDIUM"
    assert "money_request_floor" in out["flags"] and "No signals detected" not in out["flags"]
    assert out["positive_signals"] == [] and out["key_dampeners"] == [] and out["alternative_explanations"] == []
    assert out["vie_action"] == "WARN"
    ev = out["evidence_scoring"]
    assert ev["enriched_signals"] and ev["cumulative_evidence_score"] > 0
    assert str(ev["evidence_verdict"]).upper() != "NONE"
    assert out["safety_floors"]["fired"] == ["money_request_floor"]


def test_never_lowers_score_or_changes_existing_risk_lane():
    out = apply_safety_floors(_r(lane="COERCION_RISK", risk_score=92, risk_level="HIGH"),
                              "THEM: Send the codes", "stranger")
    assert out["risk_score"] == 92 and out["lane"] == "COERCION_RISK" and out["risk_level"] == "HIGH"


def test_precedence_threat_over_money():
    out = apply_safety_floors(_r(), "THEM: send 500 dollars or I post the pictures, people should know", "stranger")
    assert out["lane"] == "COERCION_RISK" and out["risk_score"] >= 75


@pytest.mark.parametrize("lane", ["BLOCKED"])
def test_blocked_untouched(lane):
    r = _r(lane=lane, risk_score=100)
    assert apply_safety_floors(r, "THEM: send the codes", "stranger") is r


def test_physical_harm_untouched():
    r = _r(lane="COERCION_RISK", risk_score=85, physical_harm_override=True)
    assert apply_safety_floors(r, "THEM: send the codes", "stranger") is r


def test_no_fire_returns_same_object():
    r = _r()
    assert apply_safety_floors(r, "THEM: tacos saturday?", "match") is r


# ---------------- coherence ----------------

def test_benign_lane_with_medium_score_flags_conflict_and_level():
    out = apply_safety_floors(_r(risk_score=58, risk_level="LOW"), "THEM: hi", "match")
    assert out["safety_floors"]["coherence"] == "lane_score_conflict"
    assert out["risk_level"] == "MEDIUM" and "lane_score_conflict" in out["flags"]
    assert out["risk_score"] == 58


def test_risk_lane_low_score_raised():
    out = apply_safety_floors(_r(lane="FRAUD", risk_score=10), "THEM: hi", "stranger")
    assert out["risk_score"] == 35 and out["safety_floors"]["coherence"] == "risk_lane_low_score_raised"


def test_internal_error_returns_input_marked(monkeypatch):
    import app.safety_floors as sf
    monkeypatch.setattr(sf, "detect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    out = sf.apply_safety_floors(_r(), "THEM: hi", "match")
    assert out["safety_floors"]["error"] == "RuntimeError" and out["lane"] == "BENIGN"


# ---------------- end to end through analyze_text (fallback engine) ----------------

def test_fallback_engine_now_catches_gift_card_rent():
    t = ("YOU: How do I pay?\nTHEM: Wire is fastest. Or gift cards if that's easier.\n"
         "THEM: Send the codes and I'll confirm the keys are ready.")
    out = analyze_text(t, relationship_type="stranger", use_llm=False)
    assert out["lane"] == "FRAUD" and out["risk_score"] >= 60


def _corpus():
    rows = []
    with open(os.path.join(ROOT, "evals", "cases.jsonl"), encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def test_corpus_no_floor_on_normal_cases():
    fp = [c["id"] for c in _corpus()
          if not (set(c["expect_lanes"]) & {"FRAUD", "COERCION_RISK", "BLOCKED"})
          and detect(c["text"], c.get("relationship_type", ""))]
    assert fp == []


def test_corpus_recall_on_scam_cases():
    risk = [c for c in _corpus()
            if set(c["expect_lanes"]) & {"FRAUD", "COERCION_RISK"}
            and not set(c["expect_lanes"]) & {"BENIGN", "RELATIONSHIP_NORMAL"}
            and c.get("min_risk", 0) >= 50 and c["id"] != "physical_harm_ex_01"]
    caught = [c["id"] for c in risk if detect(c["text"], c.get("relationship_type", ""))]
    # Floors are a backstop, not the detector; early-stage pitches with no ask are the AI's job.
    assert len(caught) / len(risk) >= 0.85, sorted(set(c["id"] for c in risk) - set(caught))
