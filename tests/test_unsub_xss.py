# Copyright (c) 2026 Ricky Sessums. All rights reserved.
import asyncio
import secrets

from app.email_reminders import _valid_token, unsubscribe_page

XSS = '"><script>alert(1)</script>'


def test_valid_token_accepts_real_token():
    assert _valid_token(secrets.token_urlsafe(24))


def test_valid_token_rejects_markup_and_bad_input():
    for bad in [XSS, "", "short", "a" * 65, None, "abc def" * 4, "a<b" * 8]:
        assert not _valid_token(bad)


def test_unsubscribe_page_does_not_reflect_markup():
    resp = asyncio.run(unsubscribe_page(t=XSS))
    assert resp.status_code == 400
    assert b"<script" not in resp.body
