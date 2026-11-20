"""Copyright © 2026 Ricky Sessums. All rights reserved.

WP2a: /followup is OFF unless FOLLOWUP_ENABLED is truthy. The reply engine is untouched.
When off: the route returns 404 with a plain message, and the result page does not offer
the "Ask about this read" box.
"""
import os

import jinja2
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.schemas import AnalysisResponse

TEMPLATES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")
ENV = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATES), autoescape=True)


def _render_result(**overrides):
    ctx = AnalysisResponse().model_dump()
    ctx.update(request_id="req-test", risk_level="LOW", suggested_replies=[],
               replies_suppressed=False, analysis_mode="standard")
    ctx.update(overrides)
    return ENV.get_template("result.html").render(**ctx)


@pytest.mark.parametrize("value", [None, "", "0", "false", "off", "no"])
def test_followup_is_404_unless_enabled(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("FOLLOWUP_ENABLED", raising=False)
    else:
        monkeypatch.setenv("FOLLOWUP_ENABLED", value)
    r = TestClient(main.app).post("/followup", json={"request_id": "x", "question": "hi", "history": []})
    assert r.status_code == 404 and r.json()["error"] == "followup_disabled"


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_followup_route_is_live_when_enabled(monkeypatch, value):
    monkeypatch.setenv("FOLLOWUP_ENABLED", value)
    r = TestClient(main.app).post("/followup", content=b"not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 422 and r.json()["error"] == "invalid_json"


def test_result_page_hides_the_ask_box_when_disabled():
    html = _render_result(followup_enabled=False)
    assert 'id="askSection"' not in html
    assert "sendFollowup" not in html


@pytest.mark.parametrize("kwargs", [{"followup_enabled": True}, {}], ids=["enabled", "undefined"])
def test_result_page_shows_the_ask_box_when_enabled_or_unset(kwargs):
    html = _render_result(**kwargs)
    assert 'id="askSection"' in html
    assert "sendFollowup" in html


def test_flag_helper_defaults_off(monkeypatch):
    monkeypatch.delenv("FOLLOWUP_ENABLED", raising=False)
    assert main._followup_enabled() is False
