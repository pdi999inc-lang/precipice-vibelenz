# Copyright © 2026 Ricky Sessums. All rights reserved.
"""
Tests for app/context_reads.py, app/consistency_gate.py and the result-page sections
they drive ("Worth watching", "Other ways to read it"), plus removal of the
"Before you read on" literacy card.

Fixture is modeled on the 2026-11-01 five-screenshot read where the page said
"Effort goes both ways" next to a narrative saying the user was doing most of the work.
"""
import os
import re

import jinja2
import pytest

from app.schemas import AnalysisResponse
from app.context_reads import build_context_reads, audit_view, MAX_ITEMS
from app import consistency_gate as cg

TEMPLATES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")
ENV = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATES), autoescape=True)

CHAT = (
    "YOU: It's Ricky. I just moved back to Charlotte and was saying hello.\n"
    "THEM: Oh hey\n"
    "YOU: Been really good. I lived in El Paso for about six months.\n"
    "THEM: Oh that’s nice\n"
    "THEM: I would’ve stayed in El Paso lol\n"
    "YOU: You drink or smoke?\n"
    "THEM: Drink\n"
    "YOU: I can have whatever you want here\n"
    "THEM: Gotta figure out what to do with my kiddos lol\n"
    "YOU: Oh I developed an app\n"
    "THEM: That’s so weird lol\n"
    "THEM: That some AI crap\n"
)


def _base(**kw):
    p = dict(
        presentation_mode="connection", lane="BENIGN", risk_score=10, degraded=False,
        extracted_text=CHAT,
        watch_items_raw=[
            {"point": "When you offered a hangout, the answer was about logistics, not a yes.",
             "quote": "Gotta figure out what to do with my kiddos lol", "speaker": "them"},
            {"point": "The app got waved off without them saying what put them off.",
             "quote": "That some AI crap", "speaker": "them"},
        ],
        alt_reads_raw=[
            {"read": "A busy parent may just be thinking about childcare before saying yes.",
             "quote": "my kiddos", "speaker": "them"},
        ],
        effort_balance_raw="you_more",
    )
    p.update(kw)
    return p


# ---------------- context_reads: safety filter ----------------

def test_verified_items_kept():
    ctx = build_context_reads(_base())
    assert [w["quote"] for w in ctx["watch"]] == ["Gotta figure out what to do with my kiddos lol", "That some AI crap"]
    assert len(ctx["alt_reads"]) == 1
    assert ctx["effort_balance"] == "you_more"
    assert ctx["watch_dropped"] == 0


def test_curly_quotes_and_case_still_verify():
    ctx = build_context_reads(_base(watch_items_raw=[
        {"point": "They'd have stayed out west.", "quote": "i would've STAYED in el paso lol", "speaker": "them"}]))
    assert len(ctx["watch"]) == 1


def test_invented_quote_is_dropped():
    ctx = build_context_reads(_base(watch_items_raw=[
        {"point": "They said they're not interested.", "quote": "I'm not interested in you", "speaker": "them"}]))
    assert ctx["watch"] == [] and ctx["watch_dropped"] == 1


@pytest.mark.parametrize("word", ["red flag", "risk", "signals", "pressure", "manipulative", "toxic"])
def test_scanner_vocabulary_dropped(word):
    ctx = build_context_reads(_base(watch_items_raw=[
        {"point": f"This is a {word} here.", "quote": "That some AI crap", "speaker": "them"}]))
    assert ctx["watch"] == []


def test_caps_and_dedupe():
    item = {"point": "Same point.", "quote": "Drink", "speaker": "them"}
    many = [dict(item, point=f"Point {i}.") for i in range(6)] + [item, item]
    ctx = build_context_reads(_base(watch_items_raw=many))
    assert len(ctx["watch"]) == MAX_ITEMS


def test_too_short_quote_dropped():
    ctx = build_context_reads(_base(watch_items_raw=[{"point": "x.", "quote": "lo", "speaker": "them"}]))
    assert ctx["watch"] == []


@pytest.mark.parametrize("bad", [None, "string", 5, [None, "x", 3]])
def test_malformed_raw_never_raises(bad):
    ctx = build_context_reads(_base(watch_items_raw=bad, alt_reads_raw=bad, effort_balance_raw=bad))
    assert ctx["watch"] == [] and ctx["alt_reads"] == [] and ctx["effort_balance"] == "unclear"


@pytest.mark.parametrize("over", [
    dict(presentation_mode="risk"), dict(lane="FRAUD"), dict(lane="COERCION_RISK"),
    dict(risk_score=60), dict(degraded=True), dict(physical_harm_override=True), dict(extracted_text=""),
])
def test_ineligible_reads_get_nothing(over):
    assert build_context_reads(_base(**over)) is None


def test_audit_view_has_no_text():
    av = audit_view(build_context_reads(_base()))
    assert av == {"schema": "ctx.v1", "watch_kept": 2, "watch_dropped": 0, "alt_kept": 1,
                  "alt_dropped": 0, "effort_balance": "you_more"}
    assert audit_view(None) is None


# ---------------- consistency gate ----------------

def _gated(**kw):
    p = _base(**kw)
    p["context_reads"] = build_context_reads(p)
    return p


def test_effort_contradiction_detected():
    p = _gated(positive_signals=["reciprocal_engagement"])
    r = cg.check_consistency(p)
    assert "effort_contradiction" in r["violations"] and r["enforced"] is False


def test_no_violation_when_even():
    p = _gated(positive_signals=["reciprocal_engagement"], effort_balance_raw="even")
    assert cg.check_consistency(p)["violations"] == []


def test_unverified_watch_and_nothing_off():
    p = _gated(watch_items_raw=[{"point": "x.", "quote": "never said this", "speaker": "them"}])
    v = cg.check_consistency(p)["violations"]
    assert "watch_unverified" in v and "nothing_off_contradicts" in v


def test_alt_reads_missing():
    assert "alt_reads_missing" in cg.check_consistency(_gated(alt_reads_raw=[]))["violations"]


def test_shadow_does_not_mutate_payload():
    p = _gated(positive_signals=["reciprocal_engagement"])
    before = repr(p)
    cg.check_consistency(p)
    assert repr(p) == before


def test_enforcement_is_suppress_only():
    p = _gated(positive_signals=["reciprocal_engagement", "warm_reception_present"],
               healthy_patterns={"chips": [{"key": "reciprocal_engagement", "title": "Effort goes both ways"},
                                           {"key": "warm_reception_present", "title": "Warm"}]})
    r = cg.check_consistency(p)
    p = cg.apply_enforcement(p, r)
    assert p["positive_signals"] == ["warm_reception_present"]
    assert [c["key"] for c in p["healthy_patterns"]["chips"]] == ["warm_reception_present"]
    assert p["mixed_signals_note"] == cg.MIXED_SIGNALS_NOTE and r["enforced"] is True


def test_flags_default(monkeypatch):
    monkeypatch.delenv("CONSISTENCY_SHADOW_ENABLED", raising=False)
    monkeypatch.delenv("CONSISTENCY_ENFORCE", raising=False)
    assert cg.shadow_enabled() is True and cg.enforce_enabled() is False
    monkeypatch.setenv("CONSISTENCY_SHADOW_ENABLED", "false")
    assert cg.shadow_enabled() is False


def test_gate_not_applicable_outside_connection():
    assert cg.check_consistency(_gated(presentation_mode="risk")) is None
    assert cg.check_consistency({"presentation_mode": "connection"}) is None


# ---------------- result page ----------------

def _render(**kw):
    ctx = AnalysisResponse().model_dump()
    ctx.update(request_id="req-test", risk_level="LOW", suggested_replies=[], replies_suppressed=False,
               analysis_mode="standard", confidence=0.8)
    ctx.update(kw)
    return ENV.get_template("result.html").render(**ctx)


def test_sections_render_with_quotes():
    p = _gated()
    html = _render(**{k: v for k, v in p.items() if not k.endswith("_raw")})
    assert "Worth watching" in html and "Other ways to read it" in html
    assert "They wrote: &ldquo;That some AI crap&rdquo;" in html
    assert "busy parent" in html
    assert "Nothing here stands out as off" not in html and "None surfaced" not in html


def test_sections_hidden_when_nothing_verified():
    html = _render(presentation_mode="connection", lane="BENIGN", context_reads=None,
                   concern_signals=[], alternative_explanations=["low information", "ordinary conversation"])
    assert 'id="watchCard"' not in html and 'id="altCard"' not in html
    assert "Nothing here stands out as off" not in html
    assert "Ordinary Conversation" not in html


def test_deterministic_concerns_still_show_without_llm():
    html = _render(presentation_mode="connection", lane="BENIGN", context_reads=None,
                   concern_signals=["blame_inversion"])
    assert 'id="watchCard"' in html and "Blame Inversion" in html


def test_literacy_card_removed():
    html = _render(presentation_mode="connection", lane="BENIGN",
                   literacy={"question": "Before you read on: what stood out to you?", "subtext": "x",
                             "options": [{"key": "a", "label": "A"}], "detected": ["a"]})
    assert "Before you read on" not in html and 'id="litCard"' not in html


def test_mixed_signals_note_renders_only_when_set():
    assert cg.MIXED_SIGNALS_NOTE not in _render(presentation_mode="connection", lane="BENIGN")
    assert "parts of this read point different ways" in _render(
        presentation_mode="connection", lane="BENIGN", mixed_signals_note=cg.MIXED_SIGNALS_NOTE)


def test_rendered_connection_extras_have_no_scanner_words():
    p = _gated()
    html = _render(**{k: v for k, v in p.items() if not k.endswith("_raw")})
    m = re.search(r'id="watchCard".*?(?=id="altCard")', html, re.S)
    text = re.sub(r"<[^>]+>", " ", m.group(0)) if m else ""
    assert not re.search(r"\b(flag|signal|risk|danger|pressure)\b", text, re.I)


# ---------------- interpreter wiring (mocked LLM, no network) ----------------

class _Blk:
    type = "text"

    def __init__(self, t):
        self.text = t


class _Msg:
    def __init__(self, t):
        self.content = [_Blk(t)]


def _fake_client(reply_json, seen):
    class _Messages:
        def create(self, **kw):
            seen.update(kw)
            return _Msg(reply_json)

    class _C:
        def __init__(self, *a, **k):
            self.messages = _Messages()
    return _C


def test_interpreter_passes_raw_extras_connection_only(monkeypatch):
    import json
    from app import interpreter
    reply = json.dumps({
        "diagnosis": "d", "reasoning": "r", "practical_next_steps": "n", "accountability": "a",
        "watch_items": [{"point": "p", "quote": "Drink", "speaker": "them"}],
        "alt_reads": [], "effort_balance": "you_more"})
    seen = {}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(interpreter._anthropic, "Anthropic", _fake_client(reply, seen))
    out = interpreter._llm_enrich({"lane": "BENIGN"}, CHAT, "connection", "d0", "r0", "n0", "a0")
    assert out["llm_enriched"] is True
    assert out["watch_items_raw"][0]["quote"] == "Drink" and out["effort_balance_raw"] == "you_more"
    assert seen["max_tokens"] == 1600 and "watch_items" in seen["system"]
    out_r = interpreter._llm_enrich({"lane": "BENIGN"}, CHAT, "risk", "d0", "r0", "n0", "a0")
    assert out_r["watch_items_raw"] is None and seen["max_tokens"] == 1024


def test_interpreter_without_key_has_no_extras(monkeypatch):
    from app import interpreter
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = interpreter._llm_enrich({}, CHAT, "connection", "d", "r", "n", "a")
    assert out["llm_enriched"] is False and "watch_items_raw" not in out
