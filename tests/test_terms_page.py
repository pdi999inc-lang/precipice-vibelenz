"""Copyright © 2026 Ricky Sessums. All rights reserved.

WP4: the Terms of Service page. Guards the things that would be wrong to publish:
the draft's internal notes, a backdated effective date, and any company attribution
(the operator is Ricky Sessums personally - no LLC was ever filed).
"""
import os
import re

import jinja2
import pytest
from fastapi.testclient import TestClient

import app.main as main

TEMPLATES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")
ENV = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATES), autoescape=True)


def _render():
    return ENV.get_template("terms.html").render(
        effective_date=main.TERMS_EFFECTIVE_DATE, contact_email=main.PRIVACY_CONTACT_EMAIL
    )


def test_terms_route_serves_the_page():
    r = TestClient(main.app).get("/terms")
    assert r.status_code == 200
    assert "Terms of Service" in r.text


@pytest.mark.parametrize("section", [
    "What the Service does", "Eligibility", "Not professional advice", "Your content",
    "Acceptable use", "Intellectual property", "Fees", "Termination", "Disclaimers",
    "Limitation of liability", "Governing law and disputes", "Changes to these Terms",
    "General", "Contact",
])
def test_every_section_is_present(section):
    assert section in _render()


def test_founder_decisions_are_reflected():
    html = _render()
    assert "the greater of (a) the amounts you paid" in html   # D3: liability cap
    assert "$50" in html
    assert "State of North Carolina" in html                   # D4: governing law
    assert "Mecklenburg County" in html
    assert "no arbitration requirement" in html.lower()
    assert "currently offered free of charge" in html          # D2: fees
    assert "18 and older" in html                              # D5: aligned to the privacy page


def test_the_teen_age_band_is_gone():
    # The draft allowed 13-17 with permission; the live privacy page says adults only.
    html = _render()
    for banned in ("13\u201317", "13-17", "parent or guardian"):
        assert banned not in html, banned


def test_no_internal_notes_or_draft_markers_are_published():
    html = _render()
    for banned in ("Consistency notes", "not for publishing", "DRAFT", "SECOND-PASS",
                   "Attorney review", "<!--"):
        assert banned not in html, banned


def test_no_company_attribution():
    html = _render()
    for banned in ("LLC", "Precipice"):
        assert banned not in html, banned
    assert "Ricky Sessums" in html


def test_effective_date_is_not_backdated_to_the_draft_date():
    # The draft said November 16, 2026. The effective date is the publish date.
    assert "November 16" not in main.TERMS_EFFECTIVE_DATE
    assert "November 16" not in _render()
    assert re.match(r"^[A-Z][a-z]+ \d{1,2}, \d{4}$", main.TERMS_EFFECTIVE_DATE), main.TERMS_EFFECTIVE_DATE


def test_makes_no_accuracy_or_compliance_claims():
    html = _render()
    assert "no accuracy claims" in html.lower()
    for banned in ("GDPR", "SOC 2", "HIPAA", "CCPA", "certified", "compliant with"):
        assert banned not in html, banned


def test_terms_is_linked_wherever_privacy_is():
    src = open(os.path.join(TEMPLATES, "index.html"), encoding="utf-8").read()
    assert src.count('href="/privacy"') == src.count('href="/terms"')
