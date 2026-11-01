"""
tests/test_gottman_patterns.py

Copyright © 2026 Ricky Sessums. All rights reserved.

Shadow-mode Gottman patterns. Every test asserts an exact outcome (no vacuous guards).
Endpoint tests mirror tests/test_vie_t.py: they drive /analyze-screenshots in-process.
"""
import asyncio
import os
import time
from unittest import mock

import httpx
import pytest

from app import main
from app.gottman_patterns import (
    ALLOWED_RELATIONSHIP_TYPES,
    GOTTMAN_VERSION,
    MAX_COUNT_PER_KEY,
    MAX_INDEXES_PER_KEY,
    POSITIVE_KEYS,
    _is_bid,
    _is_turned_toward,
    build_gottman_patterns,
    detect,
)


def _payload(text, **kw):
    base = {
        "extracted_text": text, "user_side": "right", "presentation_mode": "connection",
        "lane": "BENIGN", "risk_level": "LOW", "risk_score": 10,
        "injection_blocked": False, "subtle_patterns": None, "flags": [], "concern_signals": [],
    }
    base.update(kw)
    return base


def _keys(text):
    return sorted(detect(text)["hits"])


def _build(text, rel="current_partner", **kw):
    return build_gottman_patterns(_payload(text, **kw), rel)


# --------------------------------------------------------------------- fixtures
CONTEMPT = "YOU: I had a really hard day.\nTHEM: Lol ok\nYOU: Can we talk about it?\nTHEM: Oh poor baby."
CRITIC = "YOU: I felt hurt.\nTHEM: You never check in anymore. You don't even care.\nYOU: That's not fair.\nTHEM: What's wrong with you?"
STONE = ("YOU: Can we please talk about what happened last night? I need to understand.\nTHEM: K\n"
         "YOU: I felt left out and it really hurt me.\nTHEM: Whatever.")
DEFEND = "YOU: I felt left out.\nTHEM: It's not my fault. Maybe if you had said something.\nYOU: I did.\nTHEM: What was I supposed to do?"
GOOD_BIDS = ("YOU: How was your day?\nTHEM: Long. Tell me more about yours?\nYOU: I'm really tired today.\n"
             "THEM: Want to talk about it?\nYOU: Can we talk later tonight?\nTHEM: Yes, that works.")
BAD_BIDS = ("YOU: How was your day?\nTHEM: Ok\nYOU: I'm really tired.\nTHEM: K\n"
            "YOU: Can we talk about it?\nTHEM: Whatever.")


# ------------------------------------------------------------------ determinism / basics
def test_determinism():
    assert detect(GOOD_BIDS) == detect(GOOD_BIDS)
    assert _build(CONTEMPT) == _build(CONTEMPT)


def test_no_signal_returns_none():
    assert _build("YOU: What time?\nTHEM: 7.\nYOU: Where?\nTHEM: The usual place.") is None


def test_exact_audit_shape_and_no_text():
    r = _build(CONTEMPT)
    assert r is not None
    assert set(r) >= {"schema_version", "module_version", "config_version", "keys", "counts",
                      "evidence_indexes", "turns_evaluated", "status"}
    assert set(r) <= {"schema_version", "module_version", "config_version", "keys", "counts",
                      "evidence_indexes", "turns_evaluated", "status", "style_similarity_ratio"}
    assert r["module_version"] == GOTTMAN_VERSION and r["status"] == "ok"
    blob = str(r).lower()
    for fragment in ("lol ok", "poor baby", "hard day", "talk about it"):
        assert fragment not in blob
    assert r["keys"] == ["contempt"]
    assert r["evidence_indexes"] == {"contempt": [1, 3]}
    assert r["counts"] == {"contempt": 2}


# ------------------------------------------------------------------------- gates
@pytest.mark.parametrize("kw", [
    {"injection_blocked": True},
    {"lane": "BLOCKED"},
    {"lane": "FRAUD"},
    {"lane": "COERCION_RISK"},
    {"risk_level": "WITHHELD"},
    {"user_side": "mix"},
])
def test_gates_return_none(kw):
    assert _build(CONTEMPT, **kw) is None


@pytest.mark.parametrize("rel", ["stranger", "coworker", "close_friend", "family_member", "dating", "", None])
def test_relationship_gate(rel):
    assert _build(CONTEMPT, rel) is None


@pytest.mark.parametrize("rel", sorted(ALLOWED_RELATIONSHIP_TYPES))
def test_allowed_relationships_fire(rel):
    assert _build(CONTEMPT, rel)["keys"] == ["contempt"]


def test_safety_tier_subtle_pattern_gates_the_module():
    sub = {"safety_present": True, "keys": ["platform_migration_push"], "patterns": []}
    assert _build(CONTEMPT, subtle_patterns=sub) is None
    sub2 = {"keys": ["x"], "patterns": [{"key": "x", "tier": "safety"}]}
    assert _build(CONTEMPT, subtle_patterns=sub2) is None
    ok = {"safety_present": False, "keys": ["user_rationalizing"], "patterns": [{"key": "u", "tier": "self"}]}
    assert _build(CONTEMPT, subtle_patterns=ok)["keys"] == ["contempt"]


def test_relationship_type_falls_back_to_payload_key_but_param_wins():
    assert build_gottman_patterns(_payload(CONTEMPT, relationship_type="current_partner")) is not None
    assert build_gottman_patterns(_payload(CONTEMPT, relationship_type="stranger"), "ex") is not None
    assert build_gottman_patterns(_payload(CONTEMPT)) is None  # no relationship_type anywhere


def test_needs_two_labeled_turns_per_speaker():
    assert _build("YOU: Can we talk?\nYOU: I felt hurt.\nTHEM: Lol ok") is None
    assert _build("YOU: hi there\nTHEM: Lol ok\nYOU: I felt hurt.") is None
    assert _build("Lol ok\nOh poor baby\nwhatever you say\nlol ok") is None  # unlabeled


def test_kill_switch_is_read_per_call():
    assert _build(CONTEMPT) is not None
    for val in ("false", "0", "no", "off", "FALSE"):
        with mock.patch.dict(os.environ, {"GOTTMAN_SHADOW_ENABLED": val}):
            assert _build(CONTEMPT) is None
    with mock.patch.dict(os.environ, {"GOTTMAN_SHADOW_ENABLED": "true"}):
        assert _build(CONTEMPT) is not None


# ------------------------------------------------------------------------ contempt
def test_contempt_positive_and_emoji():
    assert _keys(CONTEMPT) == ["contempt"]
    assert _keys("YOU: I had a hard day.\nTHEM: \U0001F644\nYOU: Really.\nTHEM: whatever you say") == ["contempt"]


@pytest.mark.parametrize("text", [
    "YOU: Can we talk?\nTHEM: Sure.\nYOU: How was your day?\nTHEM: Fine.",
    "YOU: I had a hard day.\nTHEM: That sounds rough. Tell me more.\nYOU: Thanks.\nTHEM: Of course.",
])
def test_contempt_negative(text):
    assert "contempt" not in _keys(text)


def test_contempt_requires_short_full_reply():
    long = "YOU: I had a hard day.\nTHEM: lol ok but seriously tell me everything that happened today okay\nYOU: Fine.\nTHEM: Go on."
    assert "contempt" not in _keys(long)


# ----------------------------------------------------------------------- criticism
def test_criticism_positive():
    assert _keys(CRITIC) == ["criticism"]


@pytest.mark.parametrize("line", [
    "You always make me laugh.",
    "You never fail to surprise me.",
    "You always make me smile.",
    "I'm not saying you never listen.",
    "That isn't true, you never said that to me.",  # negation + not the marker form
    'She said "you always forget".',
    "My sister said you always forget things.",
])
def test_criticism_negatives_valence_negation_quoting(line):
    assert "criticism" not in _keys(f"YOU: hi there\nTHEM: {line}\nYOU: ok\nTHEM: nice")


def test_criticism_still_fires_on_trait_attack():
    assert "criticism" in _keys("YOU: hi\nTHEM: You're so selfish.\nYOU: ok\nTHEM: nice")


# --------------------------------------------------------------------- stonewalling
def test_stonewalling_positive():
    assert _keys(STONE) == ["stonewalling"]
    assert detect(STONE)["hits"]["stonewalling"] == [1, 3]


@pytest.mark.parametrize("text", [
    "YOU: What time should I pick you up from work?\nTHEM: Ok\nYOU: Where exactly?\nTHEM: Fine.",
    "YOU: Can we talk?\nTHEM: Ok\nYOU: Now?\nTHEM: Fine",              # preceding turn too short
    "YOU: Can we please talk about what happened last night?\nTHEM: Yes, give me ten minutes.\nYOU: Ok.\nTHEM: Ready.",
    "YOU: I felt left out and it really hurt me.\nTHEM: sure\nYOU: I felt left out and it really hurt me.\nTHEM: yeah",
])
def test_stonewalling_negatives(text):
    assert "stonewalling" not in _keys(text)


# --------------------------------------------------------------------- defensiveness
def test_defensiveness_positive_and_negative():
    assert _keys(DEFEND) == ["defensiveness"]
    assert "defensiveness" not in _keys("YOU: I felt left out.\nTHEM: You're right, I should have checked in.\nYOU: Thanks.\nTHEM: I hear you.")
    assert "defensiveness" not in _keys('YOU: hi\nTHEM: He said "it\'s not my fault" which is silly.\nYOU: ok\nTHEM: yes')


# ------------------------------------------------------------------- bids / turn-toward
@pytest.mark.parametrize("line,expected", [
    ("Can we talk about where we stand?", True),
    ("I felt hurt about where this is going.", True),
    ("Can we talk when you get home?", True),
    ("How was your day?", True),
    ("I'm really tired today.", True),
    ("What time should I pick you up?", False),
    ("Where?", False),
    ("What's the address?", False),
    ("Ok see you then.", False),
])
def test_bid_definition(line, expected):
    assert _is_bid(line) is expected


@pytest.mark.parametrize("bid,reply,expected", [
    ("I'm really tired today.", "Ok", False),
    ("I need to talk about us.", "K", False),
    ("I'm tired.", "Whatever.", False),
    ("Can we talk later tonight?", "I don't want to talk.", False),
    ("How was your day?", "Fine.", True),
    ("How was your day?", "Ok", False),
    ("Can we talk?", "Sure.", True),
    ("Can we talk later tonight?", "Yes, that works.", True),
    ("I'm tired.", "Want to talk about it?", True),
    ("I'm tired.", "Same.", True),
    ("I'm tired.", "Tell me more", True),
    ("I'm tired.", "What?", False),
    ("I'm tired.", "", False),
])
def test_turned_toward_rule(bid, reply, expected):
    assert _is_turned_toward(bid, reply) is expected


def test_logistics_never_count_as_bids():
    text = ("YOU: What time should I pick you up?\nTHEM: 7\nYOU: Where?\nTHEM: The usual place.\n"
            "YOU: Ok see you then.\nTHEM: Ok.")
    d = detect(text)
    assert d["bids"] == 0 and d["hits"] == {}


def test_bid_responsiveness_positive_exclusive():
    assert _keys(GOOD_BIDS) == ["bid_responsiveness"]
    assert detect(GOOD_BIDS)["hits"]["bid_responsiveness"] == [0, 2, 4]


def test_low_turn_toward_positive_exclusive():
    assert _keys(BAD_BIDS) == ["low_turn_toward"]
    assert detect(BAD_BIDS)["hits"]["low_turn_toward"] == [0, 2, 4]


def test_ratio_boundaries_and_min_bids():
    # 2 bids: below MIN_BIDS, nothing fires
    two = "YOU: How was your day?\nTHEM: Ok\nYOU: I'm really tired.\nTHEM: K"
    assert detect(two)["hits"] == {}
    # 3 bids, 1 turned-toward / 2 not: 2/3 >= 0.6 -> low_turn_toward only
    mix_low = ("YOU: How was your day?\nTHEM: Fine.\nYOU: I'm really tired.\nTHEM: K\n"
               "YOU: Can we talk about it?\nTHEM: Whatever.")
    assert _keys(mix_low) == ["low_turn_toward"]
    # 3 bids, 2 turned-toward / 1 not: bid_responsiveness only
    mix_hi = ("YOU: How was your day?\nTHEM: Fine.\nYOU: I'm really tired.\nTHEM: Want to talk about it?\n"
              "YOU: Can we talk about it?\nTHEM: Whatever.")
    assert _keys(mix_hi) == ["bid_responsiveness"]
    # 4 bids, 2/2: neither reaches 0.6
    even = ("YOU: How was your day?\nTHEM: Fine.\nYOU: I'm really tired.\nTHEM: Want to talk about it?\n"
            "YOU: Can we talk about it?\nTHEM: Whatever.\nYOU: I miss you.\nTHEM: K")
    assert detect(even)["hits"] == {}


def test_both_bid_patterns_can_never_fire_together():
    for n_ok in range(0, 7):
        lines = []
        for i in range(6):
            lines.append("YOU: I'm really tired.")
            lines.append("THEM: Want to talk about it?" if i < n_ok else "THEM: K")
        keys = _keys("\n".join(lines))
        assert not ({"bid_responsiveness", "low_turn_toward"} <= set(keys))


def test_unanswered_double_text_bid_is_not_turned_toward():
    text = ("YOU: I'm really tired.\nYOU: Can we talk?\nTHEM: Yes, now works.\n"
            "YOU: I miss you.\nTHEM: Tell me more")
    d = detect(text)
    assert d["bids"] == 3 and d["not_turned"] == 1 and d["turned"] == 2


# ----------------------------------------------------------------------------- repair
def test_repair_positive_negative():
    assert _keys("YOU: That hurt.\nTHEM: Can we start over? I'm sorry that came out wrong.\nYOU: Ok.\nTHEM: I need 20 minutes, then I'll come back.") == ["repair_attempt"]
    assert "repair_attempt" not in _keys("YOU: That hurt.\nTHEM: Sorry you feel that way.\nYOU: Ok.\nTHEM: Whatever.")


def test_repair_both_speakers():
    t = "YOU: Can we start over?\nTHEM: ok\nYOU: hi\nTHEM: we're on the same team"
    assert detect(t)["hits"]["repair_attempt"] == [0, 3]


def test_one_pattern_per_line_precedence():
    t = "YOU: I felt hurt.\nTHEM: Lol ok. You never care.\nYOU: Really?\nTHEM: Whatever."
    d = detect(t)["hits"]
    assert d == {"contempt": [1]}
    # a repair marker on a line that already fired a concern pattern does not double count
    t2 = "YOU: hi\nTHEM: You never listen. Can we start over?\nYOU: ok\nTHEM: fine"
    assert detect(t2)["hits"] == {"criticism": [1]}


# ------------------------------------------------------------------- positives suppressed
@pytest.mark.parametrize("kw", [{"risk_score": 75}, {"concern_signals": ["money_request"]}, {"risk_score": "bad"}])
def test_positives_suppressed_when_blocked_by_risk(kw):
    r = _build(GOOD_BIDS, **kw)
    assert r is None
    mixed = CONTEMPT + "\n" + GOOD_BIDS
    r2 = _build(mixed, **kw)
    assert r2 is not None
    assert not (set(r2["keys"]) & POSITIVE_KEYS)
    assert "contempt" in r2["keys"]


def test_positives_emitted_when_not_blocked():
    assert _build(GOOD_BIDS)["keys"] == ["bid_responsiveness"]


# ------------------------------------------------------------------- caps and indexes
def test_caps_and_status():
    lines = []
    for _ in range(30):
        lines += ["YOU: I felt hurt and need to talk.", "THEM: Lol ok"]
    r = _build("\n".join(lines))
    assert r["status"] == "capped"
    assert r["counts"]["contempt"] == MAX_COUNT_PER_KEY
    assert len(r["evidence_indexes"]["contempt"]) == MAX_INDEXES_PER_KEY


def test_status_ok_at_exactly_cap_boundary():
    lines = []
    for _ in range(MAX_INDEXES_PER_KEY):
        lines += ["YOU: I felt hurt and need to talk.", "THEM: Lol ok"]
    r = _build("\n".join(lines))
    assert r["status"] == "ok" and len(r["evidence_indexes"]["contempt"]) == MAX_INDEXES_PER_KEY


def test_indexes_point_at_the_expected_speaker_and_line():
    from app.subtle_patterns import parse_turns
    turns, _ = parse_turns(CRITIC)
    r = _build(CRITIC)
    for key, idxs in r["evidence_indexes"].items():
        for i in idxs:
            assert turns[i][0] == "them"
    turns, _ = parse_turns(GOOD_BIDS)
    r = _build(GOOD_BIDS)
    assert all(turns[i][0] == "you" for i in r["evidence_indexes"]["bid_responsiveness"])


def test_style_similarity_is_a_metric_only():
    t = "\n".join(["YOU: one two three four", "THEM: one two three four"] * 4)
    d = detect(t)
    assert d["style_ratio"] == 1.0 and d["hits"] == {}
    assert _build(t) is None


# ------------------------------------------------------------------- hostile input / perf
@pytest.mark.parametrize("text", [
    "\x00\x01" * 1000 + "YOU: \nTHEM: " + "A" * 5000,
    "YOU: x\n" + "THEM: " + "you never " * 2000,
    ("YOU: hi\nTHEM: lol ok\n") * 3000,
    "THEM:" * 5000,
    "YOU: \U0001F644" * 500,
    "",
    "   \n\n\t",
], ids=["nulls_long", "repeat_phrase", "repeat_turns", "bare_labels", "emoji_flood", "empty", "whitespace"])
def test_never_raises_and_is_fast(text):
    t0 = time.perf_counter()
    r = build_gottman_patterns(_payload(text), "current_partner")
    assert r is None or isinstance(r, dict)
    assert (time.perf_counter() - t0) < 2.0


@pytest.mark.parametrize("bad", [None, 5, "text", [], {"extracted_text": 5}, {"extracted_text": None}])
def test_bad_payloads_return_none(bad):
    assert build_gottman_patterns(bad, "current_partner") is None


def test_does_not_mutate_payload():
    p = _payload(CONTEMPT)
    import copy
    before = copy.deepcopy(p)
    build_gottman_patterns(p, "current_partner")
    assert p == before


# ---------------------------------------------------- endpoint: shadow-mode invariants
PASTE = CONTEMPT + "\nYOU: Okay.\nTHEM: Whatever you say."


# Captured once at import so repeated _post() calls in one test never chain spies.
_REAL_AUDIT, _REAL_ANALYZE, _REAL_INTERPRET = main.write_audit_record, main.analyze_text, main.interpret_analysis


def _post(monkeypatch, gottman=None, rel="current_partner", enable=True):
    captured = []

    def spy(*a, **k):
        captured.append(k)
        return _REAL_AUDIT(*a, **k)

    ra, ri = _REAL_ANALYZE, _REAL_INTERPRET
    monkeypatch.setattr(main, "write_audit_record", spy)
    monkeypatch.setattr(main, "analyze_text", lambda *a, **k: ra(*a, **{**k, "use_llm": False}))
    monkeypatch.setattr(main, "interpret_analysis", lambda *a, **k: ri(*a, **{**k, "use_llm": False}))
    if gottman is not None:
        monkeypatch.setattr(main, "_gottman_shadow", gottman)
    env = {"GOTTMAN_SHADOW_ENABLED": "true" if enable else "false"}

    async def run():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            return await c.post("/analyze-screenshots", data={"pasted_text": PASTE, "relationship_type": rel},
                                headers={"accept": "application/json"})

    with mock.patch.dict(os.environ, env):
        resp = asyncio.run(run())
    return resp, captured


def _decision(resp):
    j = resp.json()
    return {k: j.get(k) for k in ("risk_score", "risk_level", "lane", "vie_action", "primary_label", "flags")}


def test_endpoint_audits_gottman_and_never_exposes_it(monkeypatch):
    resp, captured = _post(monkeypatch)
    assert resp.status_code == 200
    body = resp.json()
    assert "gottman" not in body and not any("gottman" in k for k in body)
    g = captured[-1]["gottman"]
    assert g is not None and "contempt" in g["keys"]
    assert "poor baby" not in str(g).lower() and "lol ok" not in str(g).lower()


def test_endpoint_decisions_identical_with_module_on_and_off(monkeypatch):
    on, cap_on = _post(monkeypatch, enable=True)
    off, cap_off = _post(monkeypatch, enable=False)
    assert on.status_code == off.status_code == 200
    assert _decision(on) == _decision(off)
    assert cap_on[-1]["gottman"] is not None
    assert cap_off[-1]["gottman"] is None


def test_endpoint_disallowed_relationship_is_not_audited(monkeypatch):
    resp, captured = _post(monkeypatch, rel="stranger")
    assert resp.status_code == 200
    assert captured[-1]["gottman"] is None


def test_endpoint_module_exception_cannot_block_a_read(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("gottman exploded")

    resp, captured = _post(monkeypatch, gottman=boom)
    assert resp.status_code == 200
    assert captured[-1]["gottman"] is None


def test_endpoint_import_failure_leaves_app_serving(monkeypatch):
    monkeypatch.setattr(main, "_gottman_shadow", None)
    resp, captured = _post(monkeypatch)
    assert resp.status_code == 200
    assert captured[-1]["gottman"] is None


def test_audit_record_omits_key_when_none_and_writes_it_when_present(tmp_path, monkeypatch):
    from app import audit
    monkeypatch.setenv("VIBELENZ_AUDIT_DIR", str(tmp_path))
    base = dict(request_id="r1", timestamp_start=time.time(), image_count=0, ocr_char_count=0,
                result={"risk_score": 1, "lane": "BENIGN"})
    rec = audit.write_audit_record(**base)
    assert "gottman" not in rec
    rec2 = audit.write_audit_record(**{**base, "request_id": "r2"}, gottman={"keys": ["contempt"]})
    assert rec2["gottman"] == {"keys": ["contempt"]}
