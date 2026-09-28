# Copyright © 2026 Ricky Sessums. All rights reserved.
"""
"Which side are you?" picker wiring.

Guarantees:
  * default / missing value behaves exactly as before (right-aligned = YOU, prompt byte-identical);
  * "left" flips YOU/THEM in both the vision prompt and the Tesseract fallback;
  * "mix" (and any unrecognised value) never guesses: bubbles are labeled LEFT:/RIGHT:, reply
    suggestions fail closed to a generic next move, and the interpreter is told attribution is unknown;
  * the front end sends the choice, defaults to Right, and fails closed to "mix".
"""
import os
import re
import types

import pytest
from fastapi.testclient import TestClient

from app import interpreter, main as app_main, ocr
from app.reply_engine import _detect_reply_mode

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


# ---------------------------------------------------------------- normalization
@pytest.mark.parametrize("raw,expected", [
    (None, "right"), ("", "right"), ("   ", "right"),
    ("right", "right"), ("RIGHT ", "right"), ("left", "left"), ("Left", "left"), ("mix", "mix"),
    ("right_blue", "mix"), ("both", "mix"), ("<script>", "mix"), ("0", "mix"),
])
def test_normalize_user_side(raw, expected):
    assert ocr.normalize_user_side(raw) == expected


# ---------------------------------------------------------------- vision prompt
def test_default_prompt_is_byte_identical_to_legacy():
    assert ocr._vision_user_prompt("right") == ocr._VISION_USER_PROMPT
    assert "right-aligned or green bubbles = YOU" in ocr._VISION_USER_PROMPT


def test_shared_prompt_pieces_have_not_drifted_from_legacy():
    assert ocr._VISION_PROMPT_HEAD in ocr._VISION_USER_PROMPT
    assert ocr._VISION_PROMPT_TAIL in ocr._VISION_USER_PROMPT


def test_left_prompt_flips_sides_and_ignores_color():
    p = ocr._vision_user_prompt("left")
    assert "left-aligned bubbles = YOU" in p and "right-aligned bubbles = THEM" in p
    assert "Ignore bubble color" in p
    assert "green" not in p and "= YOU, left-aligned" not in p


def test_mix_prompt_labels_by_position_and_never_guesses():
    p = ocr._vision_user_prompt("mix")
    assert "LEFT: <text> or RIGHT: <text>" in p and "Do not guess who is who" in p
    assert "YOU" not in p and "THEM" not in p


# ---------------------------------------------------------------- tesseract fallback
@pytest.mark.parametrize("side,rel_x,expected", [
    ("right", 0.80, "YOU"), ("right", 0.20, "THEM"), ("right", 0.50, None),
    ("left", 0.80, "THEM"), ("left", 0.20, "YOU"), ("left", 0.50, None),
    ("mix", 0.80, "RIGHT"), ("mix", 0.20, "LEFT"), ("mix", 0.50, None),
])
def test_tesseract_position_to_speaker(side, rel_x, expected):
    assert ocr._speaker_for_position(rel_x, side) == expected


# ---------------------------------------------------------------- downstream fail-closed
def test_reply_engine_fails_closed_when_labels_are_neutral():
    assert _detect_reply_mode("LEFT: hey\nRIGHT: hi there") == "next_move"
    assert _detect_reply_mode("YOU: hey\nTHEM: hi there") == "reply"  # unchanged for labeled chats


def _fake_anthropic(captured):
    class _Msgs:
        def create(self, **kw):
            captured.update(kw)
            return types.SimpleNamespace(content=[types.SimpleNamespace(text='{"diagnosis": "d"}')])

    class _Client:
        def __init__(self, **_kw):
            self.messages = _Msgs()

    return types.SimpleNamespace(Anthropic=_Client)


@pytest.mark.parametrize("side,note_expected", [("mix", True), ("right", False), ("left", False)])
def test_interpreter_is_told_when_attribution_is_unknown(monkeypatch, side, note_expected):
    captured = {}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(interpreter, "_anthropic", _fake_anthropic(captured))
    interpreter._llm_enrich(result={}, extracted_text="LEFT: hi\nRIGHT: yo", presentation_mode="connection",
                            diagnosis="", reasoning="", practical_next_steps="", accountability="",
                            user_side=side)
    sent = captured["messages"][0]["content"]
    assert ("It is NOT known which side is the user" in sent) is note_expected
    assert ("SPEAKER LABELS" in sent) is note_expected


# ---------------------------------------------------------------- endpoint plumbing
@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("EMAIL_GATE_ENABLED", "false")
    return TestClient(app_main.app)


def _post(client, monkeypatch, **form):
    seen = {}

    def fake_ocr(image_bytes_list, user_side="right"):
        seen["user_side"] = user_side
        raise RuntimeError("stop after OCR")  # endpoint turns this into a handled error

    monkeypatch.setattr(app_main, "extract_text_from_images", fake_ocr)
    client.post("/analyze-screenshots", files={"files": ("a.png", PNG, "image/png")}, data=form)
    return seen.get("user_side")


@pytest.mark.parametrize("form,expected", [
    ({}, "right"),                          # old cached page: no field at all
    ({"user_side": "right"}, "right"),
    ({"user_side": "left"}, "left"),
    ({"user_side": "mix"}, "mix"),
    ({"user_side": "LEFT "}, "left"),
    ({"user_side": "banana"}, "mix"),       # unrecognised -> fail closed
])
def test_endpoint_passes_normalized_side_to_ocr(client, monkeypatch, form, expected):
    assert _post(client, monkeypatch, **form) == expected


# ---------------------------------------------------------------- front door
def _index():
    return open(os.path.join(ROOT, "templates", "index.html"), encoding="utf-8").read()


def test_front_door_defaults_to_right_and_is_consistent():
    src = _index()
    assert '<input type="hidden" id="selRole" value="right_blue"/>' in src
    assert '<span id="rlbl">Right / Blue or Green</span>' in src
    assert re.search(r'<div class="r-opt act" id="ro-right_blue"', src)
    assert src.count('class="r-opt act"') == 1          # exactly one active option
    assert 'id="ro-left_white"' in src and 'class="r-opt act" id="ro-left_white"' not in src


def test_front_door_sends_user_side_and_fails_closed():
    src = _index()
    m = re.search(r"fd\.append\('user_side', \(\{([^}]*)\}\)\[document\.getElementById\('selRole'\)\.value\] \|\| 'mix'\);", src)
    assert m, "user_side is not appended to the form"
    assert "left_white:'left'" in m.group(1) and "right_blue:'right'" in m.group(1) and "mix:'mix'" in m.group(1)


def test_mix_option_no_longer_claims_roles():
    src = _index()
    assert "Mix of roles" not in src
    assert src.count("Not sure / mixed") == 2


# ---------------------------------------------------------------- public OCR entrypoint -> outbound request
@pytest.mark.parametrize("side,needle,forbidden", [
    ("right", "right-aligned or green bubbles = YOU", "Ignore bubble color"),
    ("left", "left-aligned bubbles = YOU", "green bubbles = YOU"),
    ("mix", "LEFT: <text> or RIGHT: <text>", "YOU: <text>"),
])
def test_chosen_side_reaches_the_outbound_vision_request(monkeypatch, side, needle, forbidden):
    sent = {}

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"content": [{"text": "YOU: hi"}]}

    def fake_post(url, headers=None, json=None, timeout=None):
        sent["json"] = json
        return _Resp()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(ocr, "HTTPX_AVAILABLE", True)
    monkeypatch.setattr(ocr.httpx, "post", fake_post)
    ocr.extract_text_from_images([PNG], user_side=side)
    prompt_text = sent["json"]["messages"][0]["content"][1]["text"]
    assert needle in prompt_text and forbidden not in prompt_text


# ---------------------------------------------------------------- picker collapsed behind a link
def test_picker_is_collapsed_behind_a_link_by_default():
    src = _index()
    assert src.index('id="sideToggle"') < src.index('id="sidePanel"')
    assert "My messages aren't on the right</button>" in src
    assert '<div id="sidePanel" hidden>' in src
    # display:block on the link would defeat the hidden attribute without this rule
    assert ".side-link[hidden]{display:none;}" in src
    # the picker (and its options) live inside the hidden panel
    panel = src[src.index('id="sidePanel"'):src.index('<div class="sh">3. Add an optional note')]
    assert 'id="rdd"' in panel and "setRole('mix'" in panel and 'id="selRole"' not in panel


def test_hidden_default_still_submits_right_and_reveal_handler_exists():
    src = _index()
    assert '<input type="hidden" id="selRole" value="right_blue"/>' in src   # outside the hidden panel
    handler = "document.getElementById('sideToggle').addEventListener('click'"
    assert src.count(handler) == 1
    assert "document.getElementById('sidePanel').hidden=false" in src
    assert "this.hidden=true" in src                                        # link goes away, panel stays open


def test_step_numbers_stay_consecutive():
    nums = [int(n) for n in re.findall(r'class="sh">(\d)\.', _index())]
    assert nums == list(range(nums[0], nums[0] + len(nums)))
    assert "4." not in "".join(str(n) for n in nums) and max(nums) == 3
