# Copyright © 2026 Ricky Sessums. All rights reserved.
"""2026-11-05: replies sit directly under 'Your next move' in connection mode, never render
normal suggestions on a gated read, and follow de-escalation advice (no playful jab)."""
from app import reply_engine as re_
from tests.test_render_v2 import _render_result

REPLIES = [{"tone": "direct", "text": "so what are you working on right now"},
           {"tone": "cautious", "text": "i've said my piece. what do you want out of it"}]
NEXT = "Stop escalating. Ask one plain question and let them answer it."


def _conn(**kw):
    base = dict(presentation_mode="connection", lane="BENIGN", risk_level="LOW",
                practical_next_steps=NEXT, accountability="Don't mistake politeness for movement.",
                suggested_replies=REPLIES, replies_suppressed=False,
                mixed_signals_note="Heads up: parts of this read point different ways.",
                turn_analysis={"turn_count": 2, "arc": "escalating", "direction": "worsening",
                               "turns": [{"turn_number": 1, "color": "low", "label": "routine message"},
                                         {"turn_number": 2, "color": "low", "label": "routine message"}]})
    base.update(kw)
    return _render_result(**base)


def test_connection_order_next_move_then_replies_then_real_talk_then_arc():
    html = _conn()
    pos = [html.index(m) for m in ("Heads up: parts of this read", "Your next move", "What to say back",
                                   "Real talk", "Conversation arc")]
    assert pos == sorted(pos), pos
    assert html.count("What to say back") == 1


def test_connection_gated_lane_hides_normal_replies():
    for kw in ({"lane": "FRAUD"}, {"lane": "COERCION_RISK"}, {"primary_label": "physical_harm_disclosed"}):
        assert "What to say back" not in _conn(**kw), kw


def test_suppressed_safety_reply_still_renders():
    html = _conn(lane="COERCION_RISK", replies_suppressed=True,
                 suggested_replies=[{"tone": "safety", "text": "state your boundary clearly one time"}])
    assert "state your boundary clearly one time" in html


def test_risk_mode_keeps_single_bottom_reply_block():
    html = _render_result(presentation_mode="risk", lane="BENIGN", risk_level="MEDIUM",
                          suggested_replies=REPLIES, replies_suppressed=False)
    assert html.count("What to say back") == 1


def test_deescalation_detector():
    assert re_.needs_deescalation(NEXT)
    assert re_.needs_deescalation("Drop it for now and give them some space.")
    assert re_.needs_deescalation("Don't push. Let it rest.")
    assert not re_.needs_deescalation("Ask her out for Saturday.")
    assert not re_.needs_deescalation("")


def test_deescalation_prompt_and_filter():
    p = re_._build_user_prompt("YOU: hi\nTHEM: ok", "next_move", "unknown", {"practical_next_steps": NEXT})
    assert "DE-ESCALATE" in p and "No teasing" in p
    p2 = re_._build_user_prompt("YOU: hi\nTHEM: ok", "next_move", "unknown", {"practical_next_steps": "Ask her out."})
    assert "DE-ESCALATE" not in p2
    raw = [{"tone": "playful", "text": "agreeing with every reel is a pretty easy workout though"},
           {"tone": "warm", "text": "fair enough. what are you working on"},
           {"tone": "cautious", "text": "i've said my piece"}]
    out = re_._validate_suggestions(raw, deescalate=True)
    assert [o["tone"] for o in out] == ["warm", "cautious"]
    assert len(re_._validate_suggestions(raw)) == 3
