"""Copyright © 2026 Ricky Sessums. All rights reserved.

Batch 2: per-client rate limits on paid and email-sending endpoints.
"""
import asyncio

import httpx
import pytest

import app.main as main
from app import rate_limit as rl


def _post_many(path, n, headers=None, json=None):
    async def run():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            out = []
            for _ in range(n):
                out.append(await c.post(path, headers=headers or {}, json=json))
            return out
    return asyncio.run(run())


def test_window_blocks_then_frees():
    rl.RULES["_t"] = [(3, 60)]
    try:
        assert [rl.check("_t", "k", now=t) for t in (0, 1, 2)] == [None, None, None]
        wait = rl.check("_t", "k", now=10)
        assert wait == 50  # oldest hit (t=0) frees at t=60
        assert rl.check("_t", "other", now=10) is None  # keys are independent
        assert rl.check("_t", "k", now=60) is None
    finally:
        del rl.RULES["_t"]


def test_blocked_hits_do_not_extend_block():
    rl.RULES["_t"] = [(1, 60)]
    try:
        assert rl.check("_t", "k", now=0) is None
        for t in range(1, 59):
            assert rl.check("_t", "k", now=t) is not None
        assert rl.check("_t", "k", now=60) is None
    finally:
        del rl.RULES["_t"]


def test_daily_rule_applies():
    assert rl.check("analyze", "d", now=0) is None
    t = 0
    allowed = 1
    for _ in range(100):
        t += 601  # step past the 10-minute window every time
        if rl.check("analyze", "d", now=t) is None:
            allowed += 1
    assert allowed == 30


def test_email_capture_limited_with_429_and_retry_after():
    res = _post_many("/email/capture", 7, json={"email": "a@b.co"})
    codes = [r.status_code for r in res]
    assert 429 not in codes[:5]
    assert codes[5] == 429 and codes[6] == 429
    body = res[5].json()
    assert body["error"] == "rate_limited" and "few minutes" in body["message"]
    assert int(res[5].headers["retry-after"]) > 0


def test_blocked_analysis_never_reaches_handler(monkeypatch):
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise AssertionError("handler must not run")

    monkeypatch.setattr(main, "analyze_text", boom)
    rl.RULES["analyze"] = [(0, 600)]  # everything blocked
    try:
        res = _post_many("/analyze-screenshots", 1)
    finally:
        rl.RULES["analyze"] = [(8, 600), (30, 86400)]
    assert res[0].status_code == 429
    assert res[0].json()["error"] == "rate_limited"
    assert calls["n"] == 0


def test_forged_left_xff_does_not_change_key():
    # Railway appends the real address on the right; anything the client
    # put on the left must not create a fresh bucket.
    reqs = []
    for i in range(7):
        reqs.append({"x-forwarded-for": f"9.9.9.{i}, 8.8.4.4"})

    async def run():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            return [await c.post("/email/capture", headers=h, json={}) for h in reqs]
    codes = [r.status_code for r in asyncio.run(run())]
    assert codes[5] == 429


def test_client_ip_skips_internal_hops():
    class R:
        def __init__(self, h):
            self.headers = h
            self.client = None
    assert rl.client_ip(R({"x-forwarded-for": "1.2.3.4, 8.8.4.4, 10.0.0.5"})) == "8.8.4.4"
    assert rl.client_ip(R({"x-forwarded-for": "10.0.0.5", "x-real-ip": "1.1.1.1"})) == "1.1.1.1"
    assert rl.client_ip(R({})) == "unknown"


def test_get_pages_and_health_never_limited():
    rl.RULES["analyze"] = [(0, 600)]
    try:
        async def run():
            transport = httpx.ASGITransport(app=main.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
                return [(await c.get("/health")).status_code for _ in range(50)]
        assert set(asyncio.run(run())) == {200}
    finally:
        rl.RULES["analyze"] = [(8, 600), (30, 86400)]


def test_kill_switch(monkeypatch):
    monkeypatch.setenv("VL_RATE_LIMITS_ENABLED", "false")
    codes = [r.status_code for r in _post_many("/email/capture", 8, json={})]
    assert 429 not in codes


def test_fails_open_on_internal_error(monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("bug")
    monkeypatch.setattr(rl, "check", broken)
    codes = [r.status_code for r in _post_many("/email/capture", 8, json={})]
    assert 429 not in codes
