# Copyright © 2026 Ricky Sessums. All rights reserved.
"""app/llm_health.py: status from passive read tracking + rate-limited probe; /health/llm endpoint."""
import pytest

from app import llm_health as h


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    h._reset_for_tests()
    monkeypatch.setenv("VL_LLM_PROBE", "on")
    yield
    h._reset_for_tests()


OK = lambda: (True, "none")  # noqa: E731
BILL = lambda: (False, "billing")  # noqa: E731
DOWN = lambda: (False, "other")  # noqa: E731


def test_ok_with_no_reads_and_good_probe():
    assert h.status(now=1000.0, prober=OK)["status"] == "ok"


def test_probe_billing_failure():
    assert h.status(now=1000.0, prober=BILL)["status"] == "billing"


def test_probe_other_failure_is_degraded():
    assert h.status(now=1000.0, prober=DOWN)["status"] == "degraded"


def test_probe_rate_limited():
    calls = []

    def p():
        calls.append(1)
        return True, "none"
    h.status(now=1000.0, prober=p)
    h.status(now=1000.0 + 60, prober=p)
    h.status(now=1000.0 + h.PROBE_INTERVAL_S + 1, prober=p)
    assert len(calls) == 2


def test_degraded_reads_trip_status():
    for i in range(3):
        h.record_read(True, "LLM analysis failed (timeout)", now=1000.0 + i)
    assert h.status(now=1010.0, prober=OK)["status"] == "degraded"


def test_one_degraded_among_many_is_ok():
    h.record_read(True, "timeout", now=1000.0)
    for i in range(5):
        h.record_read(False, None, now=1001.0 + i)
    assert h.status(now=1010.0, prober=OK)["status"] == "ok"


def test_credit_error_in_read_is_billing_even_if_probe_ok():
    h.record_read(True, "Error code: 400 - Your credit balance is too low to access the Anthropic API", now=1000.0)
    assert h.status(now=1001.0, prober=OK)["status"] == "billing"


def test_old_reads_age_out():
    for i in range(3):
        h.record_read(True, "timeout", now=1000.0 + i)
    assert h.status(now=1000.0 + h.WINDOW_S + 10, prober=OK)["status"] == "ok"


def test_probe_disabled(monkeypatch):
    monkeypatch.setenv("VL_LLM_PROBE", "off")
    assert h.status(now=1000.0, prober=BILL)["status"] == "ok"


def test_no_content_in_status():
    h.record_read(True, "THEM: send the codes", now=1000.0)
    st = h.status(now=1001.0, prober=OK)
    assert "codes" not in repr(st) and "THEM" not in repr(st)


def test_endpoint_503_on_billing(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.setattr(h, "_default_probe", BILL)
    r = TestClient(app).get("/health/llm")
    assert r.status_code == 503 and r.json()["status"] == "billing"


def test_endpoint_200_when_ok(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.setattr(h, "_default_probe", OK)
    r = TestClient(app).get("/health/llm")
    assert r.status_code == 200 and r.json()["status"] == "ok"
