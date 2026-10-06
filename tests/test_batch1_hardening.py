"""Copyright © 2026 Ricky Sessums. All rights reserved.

Batch 1 hardening: a slow analysis must not stall other requests, and the
follow-up cap must hold even when the client sends an empty history.
"""
import asyncio
import time

import httpx
import pytest

import app.main as main


TEXT = ("THEM: Hey, how was your weekend?\nYOU: Pretty good, went hiking\n"
        "THEM: Nice, which trail?\nYOU: Crowders Mountain, you should come next time")


def test_slow_analysis_does_not_block_health(monkeypatch):
    real_analyze = main.analyze_text

    def slow_analyze(*a, **k):
        time.sleep(1.5)  # stands in for a slow blocking AI call
        k["use_llm"] = False
        return real_analyze(*a, **k)

    real_interpret = main.interpret_analysis

    def no_llm_interpret(*a, **k):
        k["use_llm"] = False
        return real_interpret(*a, **k)

    monkeypatch.setattr(main, "analyze_text", slow_analyze)
    monkeypatch.setattr(main, "interpret_analysis", no_llm_interpret)

    async def run():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            read = asyncio.create_task(c.post("/analyze-screenshots", data={"pasted_text": TEXT},
                                              headers={"accept": "application/json"}))
            t0 = time.perf_counter()
            await asyncio.sleep(0.3)  # the read is now inside the slow call
            health = await c.get("/health")
            # Wall time from just after the read started until /health answered.
            # A blocked event loop pushes this past the 1.5s slow call.
            health_secs = time.perf_counter() - t0
            resp = await read
            return health.status_code, health_secs, resp.status_code

    health_status, health_secs, read_status = asyncio.run(run())
    assert health_status == 200
    assert health_secs < 1.0, f"/health waited {health_secs:.2f}s behind a running analysis"
    assert read_status in (200, 303)


def test_followup_take_caps_at_max():
    rid = "test-cap-1"
    main._FOLLOWUP_COUNTS.pop(rid, None)
    assert all(main._followup_take(rid) for _ in range(main.MAX_FOLLOWUP_QUESTIONS))
    assert main._followup_take(rid) is False


def test_followup_cap_holds_with_empty_client_history():
    from fastapi.testclient import TestClient
    rid = "test-cap-2"
    main._FOLLOWUP_COUNTS[rid] = main.MAX_FOLLOWUP_QUESTIONS
    r = TestClient(main.app).post("/followup", json={"request_id": rid, "question": "and now?", "history": []})
    assert r.status_code == 429 and r.json()["error"] == "question_limit"
