# Copyright © 2026 Ricky Sessums. All rights reserved.
"""
Privacy page + link wiring.

Checks that /privacy renders with the operator-approved contact and date, that it does not
name the operator, that the page itself loads nothing third-party (the policy says so), that
the two links exist in the front door, and that the missing-template path fails to a plain
page instead of a 500.
"""
import os
import re

import pytest
from fastapi.testclient import TestClient

from app import main as app_main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def client():
    return TestClient(app_main.app)


def test_privacy_renders_with_contact_and_effective_date(client):
    r = client.get("/privacy")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "VibeLenz999@gmail.com" in r.text
    assert "Effective September 30, 2026" in r.text
    assert "We don&rsquo;t sell your information" in r.text


def test_privacy_does_not_name_the_operator_or_claim_an_unformed_entity(client):
    body = client.get("/privacy").text
    for banned in ("Sessums", "Ricky", "LLC"):
        assert banned not in body, banned
    # The trade name may appear only as the copyright holder.
    assert body.count("Precipice Social Intelligence") == 1
    assert "&copy; 2026 Precipice Social Intelligence. All rights reserved." in body


def test_privacy_page_loads_nothing_third_party(client):
    # The policy states "This privacy page loads no analytics and no outside fonts."
    body = client.get("/privacy").text
    assert "<script" not in body.lower()
    assert not re.search(r'(src|href)="https?://', body)  # only mailto: and same-site links
    for host in ("googletagmanager", "google-analytics", "fonts.googleapis", "fonts.gstatic"):
        assert host not in body


def test_privacy_discloses_the_known_gaps_plainly(client):
    body = client.get("/privacy").text
    assert "will not use anyone&rsquo;s conversations to improve or train VibeLenz" in body
    assert "do not currently delete analysis text on a schedule" in body
    assert "we do not currently send text messages" in body
    assert "18 and older" in body


def test_contact_email_is_env_overridable_and_escaped(client, monkeypatch):
    monkeypatch.setattr(app_main, "PRIVACY_CONTACT_EMAIL", 'x"><script>alert(1)</script>@e.com')
    body = client.get("/privacy").text
    assert "<script>alert(1)</script>" not in body


def test_missing_template_fails_to_a_plain_page_not_a_500(client, monkeypatch, tmp_path):
    monkeypatch.setattr(app_main, "TEMPLATES_DIR", tmp_path)  # no privacy.html in here
    r = client.get("/privacy")
    assert r.status_code == 200 and "not found" in r.text.lower()


def test_front_door_links_to_privacy_from_page_and_from_the_email_gate():
    src = open(os.path.join(ROOT, "templates", "index.html"), encoding="utf-8").read()
    assert src.count('href="/privacy"') == 2
    # modal link must open in a new tab so a half-filled form isn't lost
    modal = re.search(r'<a href="/privacy" target="_blank" rel="noopener"', src)
    assert modal
