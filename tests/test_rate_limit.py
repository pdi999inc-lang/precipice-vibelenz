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


class _R:
    def __init__(self, h, peer="100.64.0.3"):
        self.headers = h
        self.client = type("C", (), {"host": peer})()


def test_default_mode_is_leftmost(monkeypatch):
    monkeypatch.delenv("VL_CLIENT_IP_MODE", raising=False)
    r = _R({"x-forwarded-for": "8.8.4.4, 66.33.22.9, 10.0.0.5"})
    assert rl.client_ip(r) == "8.8.4.4"


def test_rightmost_mode_skips_internal_hops(monkeypatch):
    monkeypatch.setenv("VL_CLIENT_IP_MODE", "rightmost")
    assert rl.client_ip(_R({"x-forwarded-for": "1.2.3.4, 8.8.4.4, 10.0.0.5"})) == "8.8.4.4"


def test_realip_mode_and_bad_mode_fallback(monkeypatch):
    monkeypatch.setenv("VL_CLIENT_IP_MODE", "realip")
    assert rl.client_ip(_R({"x-real-ip": "1.1.1.1"})) == "1.1.1.1"
    monkeypatch.setenv("VL_CLIENT_IP_MODE", "nonsense")
    assert rl.ip_mode() == "leftmost"


def test_missing_headers_fall_back_to_peer(monkeypatch):
    monkeypatch.delenv("VL_CLIENT_IP_MODE", raising=False)
    assert rl.client_ip(_R({})) == "100.64.0.3"
    assert rl.client_ip(_R({"x-forwarded-for": "garbage"})) == "100.64.0.3"


def test_rotating_edge_ip_still_groups_one_client(monkeypatch):
    # The live failure: a per-request public edge address on the right made
    # every request look new under 'rightmost'. Leftmost groups them.
    monkeypatch.delenv("VL_CLIENT_IP_MODE", raising=False)

    async def run():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            return [await c.post("/email/capture", json={},
                                 headers={"x-forwarded-for": f"8.8.4.4, 66.33.22.{i}"})
                    for i in range(7)]
    codes = [r.status_code for r in asyncio.run(run())]
    assert codes[5] == 429 and codes[6] == 429


def test_diag_endpoint_requires_secret(monkeypatch):
    async def run(h):
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            return await c.get("/diag/client-ip", headers=h)
    monkeypatch.setattr(main, "STATS_SECRET", "")
    assert asyncio.run(run({"x-stats-secret": ""})).status_code == 403
    monkeypatch.setattr(main, "STATS_SECRET", "s3")
    assert asyncio.run(run({"x-stats-secret": "nope"})).status_code == 403
    r = asyncio.run(run({"x-stats-secret": "s3", "x-forwarded-for": "8.8.4.4, 66.33.22.1"}))
    assert r.status_code == 200
    body = r.json()
    assert body["key"] == "8.8.4.4" and body["by_mode"]["rightmost"] == "66.33.22.1"


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
