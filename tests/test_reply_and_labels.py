# Copyright © 2026 Ricky Sessums. All rights reserved.
"""2026-11-02 live read: unstated 'ex' scrubbed; replies anchored to the last message and the next-move advice."""
from app.interpreter import interpret_analysis, scrub_unstated_ex
from app import reply_engine as re_

CHAT = "YOU: hi\nTHEM: Thank you for letting me! And that was amazing\nYOU: when are you free\nTHEM: Gotta figure out what to do with my kiddos lol"


def test_ex_scrubbed_when_chat_never_says_it():
    assert scrub_unstated_ex("This is a casual, physically-familiar ex dynamic.", CHAT) == "This is a casual, physically-familiar dynamic."
    assert scrub_unstated_ex("The ex context matters here.", CHAT) == "The context matters here."
    assert scrub_unstated_ex("You reopened contact with an ex.", CHAT) == "You reopened contact with someone you have history with."
    assert scrub_unstated_ex("Your ex-girlfriend replied.", CHAT) == "Someone from your past replied."


def test_ex_kept_when_chat_says_it():
    assert scrub_unstated_ex("You reopened contact with an ex.", "THEM: my ex was weird") == "You reopened contact with an ex."


def test_words_containing_ex_untouched():
    t = "Exactly. Next, extra effort, expect more. Text her."
    assert scrub_unstated_ex(t, CHAT) == t


def test_connection_read_scrubs_ex(monkeypatch):
    from app import interpreter
    monkeypatch.setattr(interpreter, "_llm_enrich", lambda **k: {
        "diagnosis": "A physically-familiar ex dynamic.", "reasoning": "The ex context matters here.",
        "practical_next_steps": "Ask once.", "accountability": "Be honest.", "llm_enriched": True, "llm_error": None})
    out = interpret_analysis({"lane": "BENIGN", "risk_score": 5, "risk_level": "LOW", "primary_label": "x"},
                             extracted_text=CHAT, requested_mode="connection")
    assert " ex " not in f" {out['diagnosis']} {out['reasoning']} ".lower()
    assert out.get("relationship_label_scrubbed") is True


def test_prompt_carries_last_message_and_next_move():
    p = re_._build_user_prompt(CHAT, "reply", "female",
                               {"practical_next_steps": "Drop the app for now.", "diagnosis": "You're steering."})
    assert 'Gotta figure out what to do with my kiddos lol' in p
    assert "Drop the app for now." in p and "YOUR NEXT MOVE" in p
    assert "Never answer a question the user asked" in p


def test_scrambled_order_switches_to_next_move(monkeypatch):
    seen = {}
    monkeypatch.setattr(re_, "_call_llm", lambda text, mode, g, p: seen.setdefault("mode", mode) and [] or [{"tone": "direct", "text": "x"}])
    re_.generate_replies({"lane": "BENIGN", "diagnosis": "The chat is pasted in reverse-chronological chunks"}, CHAT)
    assert seen["mode"] == "next_move"


def test_normal_order_stays_reply(monkeypatch):
    seen = {}
    monkeypatch.setattr(re_, "_call_llm", lambda text, mode, g, p: seen.setdefault("mode", mode) and [] or [{"tone": "direct", "text": "x"}])
    re_.generate_replies({"lane": "BENIGN", "diagnosis": "Warm and steady."}, CHAT)
    assert seen["mode"] == "reply"


def test_reply_model_reads_env_and_text_blocks(monkeypatch):
    calls = {}

    class Blk:
        type = "text"
        text = '[{"tone":"direct","text":"sounds good"}]'

    class Msg:
        content = [type("T", (), {"type": "thinking", "text": None})(), Blk()]

    class C:
        def __init__(self, **k):
            self.messages = self

        def create(self, **k):
            calls.update(k)
            return Msg()
    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", C)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("VL_ENRICH_MODEL", "claude-sonnet-5-5")
    monkeypatch.delenv("VL_REPLY_MODEL", raising=False)
    out = re_._call_llm(CHAT, "reply", "unknown", {})
    assert calls["model"] == "claude-sonnet-5-5" and out == [{"tone": "direct", "text": "sounds good"}]
