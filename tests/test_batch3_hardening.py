"""Copyright © 2026 Ricky Sessums. All rights reserved.

Batch 3 hardening (2026-11-11 audit): AI call timeouts, the pitch share image served
from our own domain, and a floor under the dependency pins so a stale requirements.txt
cannot quietly bring the vulnerable versions back.
"""
import inspect
import re
from pathlib import Path

import pytest

import app.analyzer_combined as ac
import app.interpreter as interpreter
import app.main as main
import app.reply_engine as reply_engine
from app import llm_util

ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------ timeouts
def test_client_kwargs_defaults(monkeypatch):
    monkeypatch.delenv("VL_LLM_TIMEOUT_S", raising=False)
    monkeypatch.delenv("VL_LLM_MAX_RETRIES", raising=False)
    assert llm_util.client_kwargs() == {"timeout": 90.0, "max_retries": 1}


@pytest.mark.parametrize("raw,expected", [("45", 45.0), ("1", 5.0), ("99999", 600.0), ("abc", 90.0), ("", 90.0),
                                         ("nan", 90.0)],
                         ids=["set", "clamp_low", "clamp_high", "garbage", "empty", "nan"])
def test_timeout_env_is_clamped(raw, expected, monkeypatch):
    monkeypatch.setenv("VL_LLM_TIMEOUT_S", raw)
    assert llm_util.client_kwargs()["timeout"] == expected


@pytest.mark.parametrize("raw,expected", [("0", 0), ("3", 3), ("50", 5), ("-2", 0), ("x", 1)],
                         ids=["zero", "set", "clamp_high", "clamp_low", "garbage"])
def test_retry_env_is_clamped(raw, expected, monkeypatch):
    monkeypatch.setenv("VL_LLM_MAX_RETRIES", raw)
    assert llm_util.client_kwargs()["max_retries"] == expected


class _Stop(Exception):
    pass


def _capturing_client(seen):
    class _Messages:
        def create(self, **_k):
            raise _Stop("no network in tests")

    class _Client:
        def __init__(self, **k):
            seen.append(k)
            self.messages = _Messages()

    return _Client


def _assert_bounded(seen):
    assert seen, "client was never constructed"
    for k in seen:
        assert k.get("timeout") == 90.0 and k.get("max_retries") == 1, k


def test_analysis_client_is_bounded(monkeypatch):
    import anthropic
    seen = []
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.delenv("VL_LLM_TIMEOUT_S", raising=False)
    monkeypatch.delenv("VL_LLM_MAX_RETRIES", raising=False)
    monkeypatch.setattr(anthropic, "Anthropic", _capturing_client(seen))
    r = ac.analyze_text("THEM: hey how was your weekend\nYOU: good, went hiking", use_llm=True)
    _assert_bounded(seen)
    assert r["degraded"] is True  # the stubbed call failed, so the backup engine answered


def test_narrative_client_is_bounded(monkeypatch):
    seen = []
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.delenv("VL_LLM_TIMEOUT_S", raising=False)
    monkeypatch.delenv("VL_LLM_MAX_RETRIES", raising=False)
    monkeypatch.setattr(interpreter._anthropic, "Anthropic", _capturing_client(seen))
    text = "THEM: hey how was your weekend\nYOU: good, went hiking"
    analysis = ac.analyze_text(text, use_llm=False)
    out = interpreter.interpret_analysis(analysis, extracted_text=text, requested_mode="connection",
                                         relationship_type="match", use_llm=True)
    _assert_bounded(seen)
    assert out.get("diagnosis")  # deterministic copy still returned


def test_reply_client_is_bounded(monkeypatch):
    import anthropic
    seen = []
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.delenv("VL_LLM_TIMEOUT_S", raising=False)
    monkeypatch.delenv("VL_LLM_MAX_RETRIES", raising=False)
    monkeypatch.setattr(anthropic, "Anthropic", _capturing_client(seen))
    text = "THEM: hey how was your weekend\nYOU: good, went hiking"
    payload = {"lane": "BENIGN", "risk_score": 5, "risk_level": "LOW", "presentation_mode": "connection"}
    out = reply_engine.generate_replies(payload=payload, extracted_text=text, other_gender="unknown")
    _assert_bounded(seen)
    assert out["suggested_replies"] == []


def test_followup_client_is_bounded():
    src = inspect.getsource(main.followup)
    assert "anthropic.Anthropic(api_key=_api_key, **client_kwargs())" in src


def test_no_unbounded_ai_client_left_in_app():
    # llm_health sets its own short timeout; every other construction must use client_kwargs().
    pat = re.compile(r"=\s*_?anthropic\.Anthropic\(")  # real constructions, not docstrings
    found = 0
    for path in sorted((ROOT / "app").glob("*.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if pat.search(line) and not line.lstrip().startswith("#"):
                found += 1
                assert "client_kwargs()" in line or "timeout=" in line, f"{path.name}: {line.strip()}"
    assert found == 5, found  # analysis, narrative, replies, follow-up, health probe


# ------------------------------------------------------------------ dependency floor
def _pins():
    pins = {}
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        m = re.match(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?==([0-9][0-9.]*)$", line)
        if m:
            pins[m.group(1).lower()] = tuple(int(x) for x in m.group(2).split("."))
    return pins


# Lowest versions with no advisory open on 2026-11-11 (pip-audit). A stale requirements.txt
# copied over this one would fail here instead of silently shipping the old versions.
FLOORS = [("starlette", (1, 3, 1)), ("python-multipart", (0, 0, 31)), ("jinja2", (3, 1, 6)),
          ("pillow", (13, 0, 0))]


@pytest.mark.parametrize("name,floor", FLOORS, ids=[n for n, _ in FLOORS])
def test_dependency_pins_stay_above_the_patched_floor(name, floor):
    pins = _pins()
    assert name in pins, f"{name} must be pinned with == in requirements.txt"
    assert pins[name] >= floor, f"{name} pinned at {pins[name]}, patched floor is {floor}"


# ------------------------------------------------------------------ pitch share image
def test_pitch_share_image_is_served_from_our_domain():
    from fastapi.testclient import TestClient
    src = (ROOT / "templates" / "pitch.html").read_bytes().decode("utf-8")
    assert "raw.githubusercontent.com" not in src
    assert src.count('content="https://app.appvibelenz.com/static/og-image.svg"') == 2
    r = TestClient(main.app).get("/static/og-image.svg")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")


def test_no_template_depends_on_the_github_repo():
    for path in sorted((ROOT / "templates").glob("*.html")):
        assert "githubusercontent.com" not in path.read_bytes().decode("utf-8"), path.name
