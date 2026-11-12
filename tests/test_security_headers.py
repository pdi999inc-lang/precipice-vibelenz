"""Copyright © 2026 Ricky Sessums. All rights reserved.

Security headers (app/security_headers.py) and the public API-docs switch-off.
"""
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app import security_headers as sh

client = TestClient(main.app)

PAGES = ["/", "/privacy", "/pitch", "/health"]


@pytest.mark.parametrize("path", PAGES, ids=["home", "privacy", "pitch", "health"])
def test_headers_present_on_every_page(path, monkeypatch):
    monkeypatch.delenv("VL_SECURITY_HEADERS_ENABLED", raising=False)
    r = client.get(path)
    assert r.status_code == 200
    for name, value in sh.HEADERS.items():
        assert r.headers.get(name) == value, name


def test_headers_also_on_error_responses(monkeypatch):
    monkeypatch.delenv("VL_SECURITY_HEADERS_ENABLED", raising=False)
    r = client.get("/audit/stats")  # default-deny route
    assert r.status_code == 403
    assert r.headers.get("X-Frame-Options") == "DENY"
    assert r.headers.get("X-Content-Type-Options") == "nosniff"


@pytest.mark.parametrize("value", ["false", "0", "off", "NO"], ids=["false", "zero", "off", "no"])
def test_kill_switch_removes_headers(value, monkeypatch):
    monkeypatch.setenv("VL_SECURITY_HEADERS_ENABLED", value)
    r = client.get("/health")
    assert r.status_code == 200
    for name in sh.HEADERS:
        assert name not in r.headers, name


def test_csp_is_the_minimal_non_breaking_set():
    # A script/style/img policy would break the inline scripts, Google Analytics and
    # Google Fonts the pages use. It must not be added without a real-browser pass.
    csp = sh.HEADERS["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in csp
    for directive in ("script-src", "style-src", "img-src", "font-src", "connect-src", "default-src"):
        assert directive not in csp, directive


def test_hsts_is_host_only_and_not_preloaded():
    hsts = sh.HEADERS["Strict-Transport-Security"]
    assert hsts.startswith("max-age=")
    assert "includeSubDomains" not in hsts and "preload" not in hsts


def test_internal_error_fails_open(monkeypatch):
    def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(sh, "enabled", boom)
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"], ids=["docs", "redoc", "openapi"])
def test_api_docs_are_not_public(path):
    assert client.get(path).status_code == 404
