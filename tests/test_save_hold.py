"""Copyright © 2026 Ricky Sessums. All rights reserved.

WP3: POST /save sets or releases the retention hold on one analysis.

Two invariants this file exists to pin:

  1. Saving is NOT consent to training use. They are separate grants, and no save
     may set training_consent_at as a side effect. Tested at the SQL level, not
     just at the endpoint, because that is where a future refactor would break it.

  2. The endpoint does not return ok on failure. /feedback deliberately swallows
     errors and always returns ok; /save must not, because the UI must never show
     a read as kept unless it was.

Authorisation is by the per-row save_token embedded in that analysis's own result
page. email_visitors is deliberately not joined to analyses, so there is no visitor
identity to authorise against and none is introduced.
"""
import os

import jinja2
import pytest
from fastapi.testclient import TestClient

import app.db as db
import app.main as main
from app.schemas import AnalysisResponse

TEMPLATES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")
ENV = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATES), autoescape=True)

BODY = {"request_id": "req-1", "save_token": "tok-1", "hold": True}


def _render_result(**overrides):
    ctx = AnalysisResponse().model_dump()
    ctx.update(request_id="req-test", risk_level="LOW", suggested_replies=[],
               replies_suppressed=False, analysis_mode="standard", followup_enabled=False)
    ctx.update(overrides)
    return ENV.get_template("result.html").render(**ctx)


# --------------------------------------------------------------- endpoint contract

@pytest.mark.parametrize("outcome,status", [("ok", 200), ("not_found", 404), ("error", 503)])
def test_outcome_maps_to_status(monkeypatch, outcome, status):
    monkeypatch.setattr(db, "set_retention_hold", lambda *a, **k: outcome)
    r = TestClient(main.app).post("/save", json=BODY)
    assert r.status_code == status, r.text


def test_failure_never_reports_ok(monkeypatch):
    # The whole point of not copying /feedback's always-ok behaviour.
    monkeypatch.setattr(db, "set_retention_hold", lambda *a, **k: "error")
    r = TestClient(main.app).post("/save", json=BODY)
    assert r.status_code == 503 and r.json()["status"] != "ok"


def test_bad_json_is_400(monkeypatch):
    monkeypatch.setattr(db, "set_retention_hold", lambda *a, **k: "ok")
    r = TestClient(main.app).post("/save", content=b"not json",
                                  headers={"Content-Type": "application/json"})
    assert r.status_code == 400


def test_db_exception_is_503_not_a_500(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db gone")
    monkeypatch.setattr(db, "set_retention_hold", boom)
    r = TestClient(main.app).post("/save", json=BODY)
    assert r.status_code == 503


def test_token_is_never_echoed_back(monkeypatch):
    monkeypatch.setattr(db, "set_retention_hold", lambda *a, **k: "ok")
    r = TestClient(main.app).post("/save", json=BODY)
    assert "tok-1" not in r.text


# ------------------------------------------------- saving is not training consent

def _capture(monkeypatch):
    seen = {}

    def fake(request_id, save_token, hold, training_consent=None, terms_version=""):
        seen.update(request_id=request_id, save_token=save_token, hold=hold,
                    training_consent=training_consent, terms_version=terms_version)
        return "ok"
    monkeypatch.setattr(db, "set_retention_hold", fake)
    return seen


def test_save_without_the_checkbox_passes_consent_none(monkeypatch):
    seen = _capture(monkeypatch)
    TestClient(main.app).post("/save", json=BODY)
    assert seen["training_consent"] is None


def test_save_with_explicit_null_passes_consent_none(monkeypatch):
    seen = _capture(monkeypatch)
    TestClient(main.app).post("/save", json={**BODY, "training_consent": None})
    assert seen["training_consent"] is None


def test_consent_true_is_passed_through_with_the_terms_version(monkeypatch):
    seen = _capture(monkeypatch)
    TestClient(main.app).post("/save", json={**BODY, "training_consent": True})
    assert seen["training_consent"] is True
    assert seen["terms_version"] == main.TERMS_EFFECTIVE_DATE


def test_consent_false_withdraws(monkeypatch):
    seen = _capture(monkeypatch)
    TestClient(main.app).post("/save", json={**BODY, "training_consent": False})
    assert seen["training_consent"] is False


def test_release_passes_hold_false(monkeypatch):
    seen = _capture(monkeypatch)
    TestClient(main.app).post("/save", json={**BODY, "hold": False})
    assert seen["hold"] is False


# ------------------------------------------------------------------- the db layer

class _FakeCursor:
    def __init__(self, sink):
        self.sink = sink
        self.rowcount = 1

    def execute(self, sql, params=None):
        self.sink.append((sql, params))

    def close(self):
        pass


class _FakeConn:
    def __init__(self, sink):
        self.sink = sink

    def cursor(self):
        return _FakeCursor(self.sink)

    def commit(self):
        pass

    def close(self):
        pass


def _sql_for(monkeypatch, **kw):
    sink = []
    monkeypatch.setattr(db, "get_conn", lambda: _FakeConn(sink))
    out = db.set_retention_hold("req-1", "tok-1", kw.pop("hold", True), **kw)
    assert out == "ok"
    assert len(sink) == 1
    return sink[0]


def test_sql_never_touches_consent_when_none(monkeypatch):
    sql, params = _sql_for(monkeypatch, training_consent=None)
    assert "training_consent_at" not in sql
    assert "training_consent_terms" not in sql
    assert "retention_hold_at = NOW()" in sql


def test_sql_records_consent_with_terms_when_true(monkeypatch):
    sql, params = _sql_for(monkeypatch, training_consent=True, terms_version="November 20, 2026")
    assert "training_consent_at = NOW()" in sql
    assert "training_consent_terms = %s" in sql
    assert params[0] == "November 20, 2026"


def test_sql_clears_consent_when_false(monkeypatch):
    sql, params = _sql_for(monkeypatch, training_consent=False)
    assert "training_consent_at = NULL" in sql


def test_sql_releases_the_hold(monkeypatch):
    sql, params = _sql_for(monkeypatch, hold=False)
    assert "retention_hold_at = NULL" in sql


def test_sql_requires_both_request_id_and_token(monkeypatch):
    sql, params = _sql_for(monkeypatch)
    assert "request_id = %s" in sql and "save_token = %s" in sql
    assert "save_token IS NOT NULL" in sql
    # set_retention_hold passes tuple(params), so compare as a tuple, not a list.
    assert tuple(params[-2:]) == ("req-1", "tok-1")


@pytest.mark.parametrize("rid,tok", [("", "tok-1"), ("req-1", ""), ("", "")])
def test_missing_id_or_token_is_not_found_without_touching_the_db(monkeypatch, rid, tok):
    def no_db():
        raise AssertionError("get_conn must not be called")
    monkeypatch.setattr(db, "get_conn", no_db)
    assert db.set_retention_hold(rid, tok, True) == "not_found"


def test_no_connection_is_error_not_silent_success(monkeypatch):
    monkeypatch.setattr(db, "get_conn", lambda: None)
    assert db.set_retention_hold("req-1", "tok-1", True) == "error"


def test_zero_rows_updated_is_not_found(monkeypatch):
    sink = []

    class _Zero(_FakeCursor):
        def __init__(self, s):
            super().__init__(s)
            self.rowcount = 0

    class _Conn(_FakeConn):
        def cursor(self):
            return _Zero(self.sink)

    monkeypatch.setattr(db, "get_conn", lambda: _Conn(sink))
    assert db.set_retention_hold("req-1", "wrong-token", True) == "not_found"


# --------------------------------------------------------------------- result page

def test_save_control_is_absent_without_a_token():
    html = _render_result(save_enabled=False, save_token="")
    assert "Keep this read" not in html and 'id="save-box"' not in html


def test_save_control_renders_with_a_token():
    html = _render_result(save_enabled=True, save_token="TOK-XYZ")
    assert 'id="save-box"' in html
    assert "Keep this read" in html
    assert "TOK-XYZ" in html


def test_consent_checkbox_is_unticked_and_separate():
    html = _render_result(save_enabled=True, save_token="TOK-XYZ")
    box = html[html.index('id="save-box"'):]
    assert 'id="save-consent"' in box
    assert "checked" not in box[:box.index("</label>")]
    assert "Optional" in box


def test_save_copy_states_the_retention_period():
    html = _render_result(save_enabled=True, save_token="TOK-XYZ")
    assert "90 days" in html
