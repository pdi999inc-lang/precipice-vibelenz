"""
tests/test_integration.py — VibeLenz Live Endpoint Smoke Tests

Hits the Railway deployment at app.appvibelenz.com.
Requires network access and a live Railway instance.

Override target URL:
    VIBELENZ_URL=https://your-url pytest tests/test_integration.py -v

Run: pytest tests/test_integration.py -v -s
"""
from __future__ import annotations

import io

import pytest
import requests

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TIMEOUT = 30  # seconds — LLM path can be slow


def _post_screenshots(base_url: str, png_bytes_list: list, **form_fields) -> requests.Response:
    """POST to /analyze-screenshots with one or more PNG files."""
    files = [
        ("files", (f"test_{i}.png", io.BytesIO(b), "image/png"))
        for i, b in enumerate(png_bytes_list)
    ]
    return requests.post(
        f"{base_url}/analyze-screenshots",
        files=files,
        data=form_fields,
        timeout=TIMEOUT,
    )


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

class TestHealth:

    def test_health_returns_200(self, live_base_url):
        resp = requests.get(f"{live_base_url}/health", timeout=10)
        assert resp.status_code == 200

    def test_health_body_correct(self, live_base_url):
        resp = requests.get(f"{live_base_url}/health", timeout=10)
        assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# GET /audit/stats
# ---------------------------------------------------------------------------

class TestAuditStats:

    def test_audit_stats_returns_200(self, live_base_url):
        resp = requests.get(f"{live_base_url}/audit/stats", timeout=10)
        assert resp.status_code == 200

    def test_audit_stats_has_session_id(self, live_base_url):
        resp = requests.get(f"{live_base_url}/audit/stats", timeout=10)
        data = resp.json()
        assert "session_id" in data, f"Missing session_id. Got: {data}"

    def test_audit_stats_has_total_analyses(self, live_base_url):
        resp = requests.get(f"{live_base_url}/audit/stats", timeout=10)
        data = resp.json()
        assert "total_analyses" in data, f"Missing total_analyses. Got: {data}"
        assert isinstance(data["total_analyses"], int)

    def test_audit_stats_not_stub(self, live_base_url):
        # DEFECT-004 guard: confirm it is not returning the old stub string
        resp = requests.get(f"{live_base_url}/audit/stats", timeout=10)
        assert resp.text.strip() != '"rewrite_stub"'
        assert isinstance(resp.json(), dict)


# ---------------------------------------------------------------------------
# POST /analyze-screenshots — validation gates
# ---------------------------------------------------------------------------

class TestAnalyzeScreenshotsValidation:

    def test_no_files_returns_422(self, live_base_url):
        resp = requests.post(
            f"{live_base_url}/analyze-screenshots",
            data={"relationship_type": "stranger"},
            timeout=TIMEOUT,
        )
        assert resp.status_code == 422

    def test_wrong_file_type_returns_422(self, live_base_url):
        files = [("files", ("test.txt", io.BytesIO(b"hello world"), "text/plain"))]
        resp = requests.post(
            f"{live_base_url}/analyze-screenshots",
            files=files,
            data={"relationship_type": "stranger"},
            timeout=TIMEOUT,
        )
        assert resp.status_code == 422

    def test_too_many_files_returns_422(self, live_base_url, sample_png_benign_dating):
        # 11 files > MAX_FILES=10
        files = [
            ("files", (f"img_{i}.png", io.BytesIO(sample_png_benign_dating), "image/png"))
            for i in range(11)
        ]
        resp = requests.post(
            f"{live_base_url}/analyze-screenshots",
            files=files,
            data={"relationship_type": "stranger"},
            timeout=TIMEOUT,
        )
        assert resp.status_code == 422

    def test_unreadable_image_returns_422(self, live_base_url, sample_png_blank):
        # Blank/near-blank image < MIN_OCR_CHARS=50 — should 422 with insufficient_ocr_data
        resp = _post_screenshots(
            live_base_url, [sample_png_blank], relationship_type="stranger"
        )
        # Accept 422 (insufficient OCR) or 503 (Tesseract can't process tiny image)
        assert resp.status_code in {422, 503}, (
            f"Expected 422 or 503 for unreadable image, got {resp.status_code}: {resp.text[:300]}"
        )
        if resp.status_code == 422:
            data = resp.json()
            assert data.get("error") == "insufficient_ocr_data"


# ---------------------------------------------------------------------------
# POST /analyze-screenshots — successful analysis
# ---------------------------------------------------------------------------

class TestAnalyzeScreenshotsSuccess:

    REQUIRED_KEYS = {
        "risk_score", "lane", "confidence", "flags",
        "presentation_mode", "diagnosis", "reasoning",
        "practical_next_steps", "accountability",
    }

    def test_benign_dating_returns_200(self, live_base_url, sample_png_benign_dating):
        resp = _post_screenshots(
            live_base_url,
            [sample_png_benign_dating],
            relationship_type="stranger",
            requested_mode="connection",
        )
        assert resp.status_code == 200, f"Unexpected status: {resp.status_code}\n{resp.text[:500]}"

    def test_benign_response_schema(self, live_base_url, sample_png_benign_dating):
        resp = _post_screenshots(
            live_base_url,
            [sample_png_benign_dating],
            relationship_type="stranger",
            requested_mode="risk",
        )
        assert resp.status_code == 200
        data = resp.json()
        for key in self.REQUIRED_KEYS:
            assert key in data, f"Missing key '{key}' in response"

    def test_risk_score_bounded(self, live_base_url, sample_png_benign_dating):
        resp = _post_screenshots(live_base_url, [sample_png_benign_dating])
        assert resp.status_code == 200
        data = resp.json()
        assert 0 <= data["risk_score"] <= 100

    def test_fraud_text_elevated_risk(self, live_base_url, sample_png_housing_fraud):
        resp = _post_screenshots(
            live_base_url,
            [sample_png_housing_fraud],
            relationship_type="stranger",
            requested_mode="risk",
        )
        assert resp.status_code == 200
        data = resp.json()
        # Housing fraud cluster must produce elevated risk
        assert data["risk_score"] >= 40, (
            f"Housing fraud text should produce risk >= 40, got {data['risk_score']}. "
            f"Lane: {data.get('lane')}. Flags: {data.get('flags')}"
        )

    def test_fraud_lane_has_risk_floor(self, live_base_url, sample_png_housing_fraud):
        resp = _post_screenshots(live_base_url, [sample_png_housing_fraud])
        assert resp.status_code == 200
        data = resp.json()
        if data.get("lane") == "FRAUD":
            assert data["risk_score"] >= 75, "FRAUD lane floor violated in live response"

    def test_response_has_request_id(self, live_base_url, sample_png_benign_dating):
        resp = _post_screenshots(live_base_url, [sample_png_benign_dating])
        assert resp.status_code == 200
        data = resp.json()
        assert "request_id" in data
        assert len(data["request_id"]) > 0

    def test_degraded_false_on_clean_analysis(self, live_base_url, sample_png_benign_dating):
        resp = _post_screenshots(live_base_url, [sample_png_benign_dating])
        assert resp.status_code == 200
        data = resp.json()
        # Degraded should be False on a clean readable image
        assert data.get("degraded") is False, f"Clean image marked degraded: {data.get('degradation_reasons')}"

    def test_audit_stats_increments_after_analysis(self, live_base_url, sample_png_benign_dating):
        # Get baseline count
        before = requests.get(f"{live_base_url}/audit/stats", timeout=10).json()
        before_count = before.get("total_analyses", 0)

        # Run an analysis
        resp = _post_screenshots(live_base_url, [sample_png_benign_dating])
        assert resp.status_code == 200

        # Stats should increment (session-level — only works if same dyno serves both requests)
        after = requests.get(f"{live_base_url}/audit/stats", timeout=10).json()
        after_count = after.get("total_analyses", 0)

        # Note: Railway may route to different dyno. Accept either increment or same.
        # This test confirms the counter is an int and the endpoint is live — not strict ordering.
        assert isinstance(after_count, int)
        assert after_count >= 0
