"""Copyright © 2026 Ricky Sessums. All rights reserved."""
import pytest

from app.analyzer_combined import analyze_text, detect_physical_harm
from app.interpreter import interpret_analysis

ABUSE = (
    "YOU: I've been observing your behavior for a while\n"
    "THEM: So you're in a domestic abuse relationship?\n"
    "THEM: ?\nTHEM: ??\nYOU: Very clearly\n"
    "THEM: I have hit you once and I regret it\n"
    "YOU: Not the slap. All the other stuff. Blocking doors, following me room to room\n"
    "THEM: Then why are you with me if I'm so controlling and manipulative or a liar\n"
    "THEM: if you want your time alone just have it"
)


@pytest.mark.parametrize("text", [
    "I have hit you once", "he slapped me last night", "she choked him", "you shoved me into the wall",
    "he put his hands on me", "this is domestic violence", "he threatened to kill me",
    "I'll kill you if you leave", "he blocked the door", "she wouldn't let me leave",
])
def test_detects_physical_harm(text):
    assert detect_physical_harm(text)


@pytest.mark.parametrize("text", [
    "hit me up later", "I'll hit you back tomorrow", "that song hit different", "hit you with the details",
    "you killed it tonight", "this traffic is killing me", "we beat them at trivia", "I don't want to hurt you",
    "can you block the spam calls", "let me know when you leave",
])
def test_ignores_everyday_phrases(text):
    assert detect_physical_harm(text) == []


@pytest.mark.parametrize("rel", ["ex", "current_partner", "match", "family_member", "stranger"])
def test_abuse_read_is_forced_into_safety_mode_with_resources(rel):
    res = analyze_text(ABUSE, relationship_type=rel, use_llm=False)
    assert res["lane"] == "COERCION_RISK" and res["risk_level"] == "HIGH" and res["risk_score"] >= 85
    assert "physical_harm_disclosed" in res["flags"]
    out = interpret_analysis(res, extracted_text=ABUSE, relationship_type=rel, requested_mode="connection", use_llm=False)
    assert out["presentation_mode"] == "risk"
    assert "1-800-799-7233" in out["practical_next_steps"]
    assert "casual" not in str(out.get("human_label", "")).lower()


def test_override_survives_any_upstream_result():
    from app.analyzer_combined import _apply_physical_harm_override
    benign = {"lane": "BENIGN", "risk_score": 5, "risk_level": "LOW", "flags": ["No signals detected"]}
    out = _apply_physical_harm_override(benign, ABUSE)
    assert out["lane"] == "COERCION_RISK" and out["flags"][0] == "physical_harm_disclosed"
    blocked = {"lane": "BLOCKED", "risk_score": 100}
    assert _apply_physical_harm_override(blocked, ABUSE) is blocked


@pytest.mark.parametrize("text", ["I beat you at chess again", "they hit me with the bill"])
def test_game_and_idiom_phrases_are_not_harm(text):
    assert detect_physical_harm(text) == []


REAL_SCREENSHOT_TEXT = (
    "THEM: 4-6 week observation window?\nTHEM: So what you been \u201cobserving\u201d my behavior\n"
    "YOU: That\u2019s all you could see\nTHEM: So you\u2019re in a domestic abuse relationship\nTHEM: ?\nTHEM: ??\n"
    "YOU: Very clearly\nTHEM: I have hit you once\nTHEM: And I regret it\nYOU: This\nYOU: Not the slap. All the other stuff\n"
    "THEM: Then why are you with me if I\u2019m so \u201ccontrolling\u201d and manipulative or a liar\n"
    "THEM: God damn if you want your time alone just have it\n"
    "YOU: See? You won\u2019t acknowledge the blocking doors, following me room to room, standing over me\n"
    "THEM: I seen that\nTHEM: I just read that\nTHEM: What is making you bring that up?\n"
    "THEM: Bring up something that happened in the past."
)


def test_real_screenshot_conversation_is_caught_on_multiple_independent_signals():
    hits = [h.lower() for h in detect_physical_harm(REAL_SCREENSHOT_TEXT)]
    assert any("hit you" in h for h in hits)
    assert any("domestic abuse" in h for h in hits)
    assert any("blocking doors" in h for h in hits)
    assert any("room to room" in h for h in hits)
    assert any("standing over me" in h for h in hits)


def test_per_screenshot_timeline_flags_harm_instead_of_low_and_stable():
    from app.analyzer_combined import analyze_turns
    chunks = [
        "THEM: So you\u2019re in a domestic abuse relationship\nYOU: Very clearly\nTHEM: I have hit you once\nTHEM: And I regret it",
        "YOU: Not the slap. All the other stuff\nTHEM: Then why are you with me",
        "THEM: What is making you bring that up?\nTHEM: Bring up something that happened in the past.",
    ]
    res = analyze_turns(chunks, relationship_type="ex")
    assert res["turns"][0]["verdict"] == "High concern"
    assert res["turns"][0]["label"] == "physical harm disclosed"
    assert res["arc"] == "physical_harm" and "Low and stable" not in res["arc_label"]


def test_harm_read_has_no_contradicting_reassurance_on_the_page():
    import re
    from jinja2 import Environment, FileSystemLoader
    from pathlib import Path
    res = analyze_text(ABUSE, relationship_type="ex", use_llm=False)
    assert res["key_dampeners"] == [] and res["alternative_explanations"] == []
    assert res["evidence_scoring"]["evidence_verdict"] == "ELEVATED"
    out = interpret_analysis(res, extracted_text=ABUSE, relationship_type="ex", requested_mode="connection", use_llm=False)
    tdir = Path(__file__).resolve().parent.parent / "templates"
    html = Environment(loader=FileSystemLoader(str(tdir))).get_template("result.html").render(**out)
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S)))
    assert "1-800-799-7233" in text
    for bad in ("Check their photos", "What lowers concern", "No risk signals detected", "Ordinary Conversation"):
        assert bad not in text, bad


def test_harm_card_leads_with_resources_not_the_explanation():
    import re
    from jinja2 import Environment, FileSystemLoader
    from pathlib import Path
    res = analyze_text(ABUSE, relationship_type="ex", use_llm=False)
    out = interpret_analysis(res, extracted_text=ABUSE, relationship_type="ex", requested_mode="connection", use_llm=False)
    tdir = Path(__file__).resolve().parent.parent / "templates"
    html = Environment(loader=FileSystemLoader(str(tdir))).get_template("result.html").render(**out)
    card = html.split('class="dash-title"', 1)[1].split('class="gauge-card"', 1)[0]
    assert "1-800-799-7233" in card
    assert "Someone being hit" not in card          # explanation moved out of the top card
    assert html.count("Someone being hit") == 1      # still shown once, under "Why it was flagged"
