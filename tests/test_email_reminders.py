"""
Copyright © 2026 Ricky Sessums. All rights reserved.

Tests for app/email_reminders.py. Uses an in-memory sqlite shim in place of
Postgres (SQL is plain, %s placeholders, timestamps passed in from Python).
Run: python -m pytest tests/test_email_reminders.py -v
"""
import re
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import email_reminders as er  # in the repo this is: from app import email_reminders as er

_REAL_SEND = er._send_email  # captured before fixtures monkeypatch it
_REAL_CONNECT = er._connect


# ---------------------------------------------------------------- sqlite shim
class _Cur:
    def __init__(self, cur):
        self._c = cur

    def execute(self, sql, params=()):
        sql = sql.replace("%s", "?").replace("TIMESTAMPTZ", "TEXT")
        params = tuple(p.isoformat() if isinstance(p, datetime) else p for p in params)
        self._c.execute(sql, params)

    def fetchone(self):
        return self._c.fetchone()

    def fetchall(self):
        return self._c.fetchall()


class _Conn:
    def __init__(self, conn):
        self._conn = conn

    def cursor(self):
        return _Cur(self._conn.cursor())

    def commit(self):
        self._conn.commit()

    def close(self):
        pass  # keep the shared in-memory DB alive across calls


@pytest.fixture(autouse=True)
def db(monkeypatch):
    raw = sqlite3.connect(":memory:", check_same_thread=False)
    shim = _Conn(raw)
    monkeypatch.setattr(er, "_connect", lambda: shim)
    monkeypatch.setattr(er, "_schema_ready", False)
    monkeypatch.setenv("EMAIL_GATE_ENABLED", "true")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://app.appvibelenz.com")
    return raw


OUTBOX = []


@pytest.fixture(autouse=True)
def outbox(monkeypatch):
    OUTBOX.clear()

    def fake_send(to, subject, text, html, unsub_url=None):
        OUTBOX.append({"to": to, "subject": subject, "text": text, "html": html, "unsub": unsub_url})
        return True, None

    monkeypatch.setattr(er, "_send_email", fake_send)
    return OUTBOX


@pytest.fixture
def client():
    app = FastAPI()
    app.middleware("http")(er.email_gate_middleware)
    app.include_router(er.router)

    from fastapi import Request
    from fastapi.responses import JSONResponse

    @app.post("/analyze-screenshots")
    async def analyze(request: Request):
        if request.headers.get("x-fail"):
            return JSONResponse({"error": "unreadable"}, status_code=422)
        return {"ok": True}

    @app.post("/followup")
    async def followup():
        return {"answer": "ok"}

    @app.get("/")
    async def home():
        return {"home": True}

    @app.get("/scam-check")
    async def scam_check():
        return {"home": True}

    return TestClient(app, base_url="https://app.appvibelenz.com")


def _rows(db):
    return db.execute("SELECT visitor_id, use_count, email, confirmed_at, reminder_count, "
                      "unsubscribed_at FROM email_visitors").fetchall()


# ---------------------------------------------------------------- gate
def test_first_use_allowed_and_cookie_issued(client):
    r = client.post("/analyze-screenshots")
    assert r.status_code == 200
    assert er.COOKIE_NAME in r.cookies


def test_second_use_blocked_without_email(client):
    client.post("/analyze-screenshots")
    r = client.post("/analyze-screenshots")
    assert r.status_code == 403
    assert r.json()["error"] == "email_required"


def test_failed_analysis_does_not_count_as_a_use(client):
    assert client.post("/analyze-screenshots", headers={"x-fail": "1"}).status_code == 422
    assert client.post("/analyze-screenshots").status_code == 200
    assert client.post("/analyze-screenshots").status_code == 403


def test_capture_unblocks(client):
    client.post("/analyze-screenshots")
    assert client.post("/analyze-screenshots").status_code == 403
    r = client.post("/email/capture", json={"email": "Person@Example.com "})
    assert r.status_code == 200
    assert client.post("/analyze-screenshots").status_code == 200


def test_capture_without_prior_use_rejected(client):
    r = client.post("/email/capture", json={"email": "a@b.co"})
    assert r.status_code == 400 and r.json()["error"] == "no_session"


@pytest.mark.parametrize("bad", ["", "nope", "a@b", "a b@c.com", "x" * 300 + "@a.com", None, 5])
def test_invalid_emails_rejected(client, bad):
    client.post("/analyze-screenshots")
    assert client.post("/email/capture", json={"email": bad}).status_code == 400


def test_forged_cookie_is_ignored(client):
    client.cookies.set(er.COOKIE_NAME, "short")
    assert client.post("/analyze-screenshots").status_code == 200


def test_kill_switch_disables_gate(client, monkeypatch):
    client.post("/analyze-screenshots")
    monkeypatch.setenv("EMAIL_GATE_ENABLED", "false")
    assert client.post("/analyze-screenshots").status_code == 200


def test_gate_fails_open_when_db_breaks(client, monkeypatch):
    client.post("/analyze-screenshots")

    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(er, "_connect", boom)
    r = client.post("/analyze-screenshots")
    assert r.status_code == 200  # a broken email module must never block analysis


# ---------------------------------------------------------------- confirm / unsub
def _capture(client, email="p@example.com"):
    client.post("/analyze-screenshots")
    client.post("/email/capture", json={"email": email})


def test_confirmation_sent_once_and_generic(client, outbox):
    _capture(client)
    assert len(outbox) == 1 and outbox[0]["to"] == "p@example.com"
    client.post("/email/capture", json={"email": "p@example.com"})  # idempotent
    assert len(outbox) == 1


def test_confirm_link_confirms(client, outbox, db):
    _capture(client)
    token = re.search(r"confirm\?t=([\w-]+)", outbox[0]["text"]).group(1)
    assert client.get(f"/email/confirm?t={token}").status_code == 200
    assert _rows(db)[0][3] is not None
    assert client.get("/email/confirm?t=bogus").status_code == 400


def test_unsubscribe_get_does_not_unsubscribe_but_post_does(client, outbox, db):
    _capture(client)
    token = re.search(r"unsubscribe\?t=([\w-]+)", outbox[0]["text"]).group(1)
    assert client.get(f"/email/unsubscribe?t={token}").status_code == 200
    assert _rows(db)[0][5] is None            # prefetch-safe
    assert client.post(f"/email/unsubscribe?t={token}").status_code == 200
    assert _rows(db)[0][5] is not None


def test_confirmation_send_cap(client, outbox):
    client.post("/analyze-screenshots")
    for i in range(6):
        client.post("/email/capture", json={"email": f"p{i}@example.com"})
        # bypass the 60s gap so only the lifetime cap is under test
        er._exec("UPDATE email_visitors SET last_confirm_sent_at = NULL")
    assert len(outbox) == er.MAX_CONFIRM_SENDS


# ---------------------------------------------------------------- reminders
def _confirmed_visitor(client, outbox):
    _capture(client)
    token = re.search(r"confirm\?t=([\w-]+)", outbox[0]["text"]).group(1)
    client.get(f"/email/confirm?t={token}")
    outbox.clear()


def _now_plus(hours):
    return datetime.now(timezone.utc) + timedelta(hours=hours)


def test_no_reminder_before_confirmation(client, outbox):
    _capture(client)
    outbox.clear()
    assert er.run_reminders(now=_now_plus(48))["sent"] == 0


def test_no_reminder_before_24h_inactivity(client, outbox):
    _confirmed_visitor(client, outbox)
    assert er.run_reminders(now=_now_plus(2))["sent"] == 0


def test_daily_cadence_and_seven_day_cap(client, outbox):
    _confirmed_visitor(client, outbox)
    total = 0
    for day in range(1, 12):
        total += er.run_reminders(now=_now_plus(24 * day))["sent"]
    assert total == er.MAX_REMINDERS
    assert len(outbox) == er.MAX_REMINDERS


def test_same_day_double_run_sends_once(client, outbox):
    _confirmed_visitor(client, outbox)
    er.run_reminders(now=_now_plus(25))
    er.run_reminders(now=_now_plus(26))
    assert len(outbox) == 1


def test_return_visit_stops_reminders_then_resumes_after_absence(client, outbox, db):
    _confirmed_visitor(client, outbox)
    er.run_reminders(now=_now_plus(25))
    assert len(outbox) == 1
    client.get("/")                                   # visitor returns
    assert _rows(db)[0][4] == 0                       # count reset
    er.run_reminders(now=_now_plus(26))               # <24h since return
    assert len(outbox) == 1
    er.run_reminders(now=_now_plus(24 * 3))           # gone again
    assert len(outbox) == 2


def test_unsubscribed_never_sent(client, outbox):
    _confirmed_visitor(client, outbox)
    er._exec("UPDATE email_visitors SET unsubscribed_at = %s", (datetime.now(timezone.utc),))
    assert er.run_reminders(now=_now_plus(48))["sent"] == 0


def test_send_failure_recorded_and_not_retried_same_day(client, outbox, db):
    _confirmed_visitor(client, outbox)
    res = er.run_reminders(now=_now_plus(25), sender=lambda *a, **k: (False, "http_500"))
    assert res["failed"] == 1
    assert db.execute("SELECT last_send_error FROM email_visitors").fetchone()[0] == "http_500"
    assert er.run_reminders(now=_now_plus(26))["sent"] == 0   # claimed, no duplicate


def test_unconfigured_sendgrid_fails_closed(monkeypatch):
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    monkeypatch.delenv("EMAIL_FROM_ADDRESS", raising=False)
    ok, err = _REAL_SEND("a@b.co", "s", "t", "h")
    assert ok is False and err == "sendgrid_not_configured"


# ---------------------------------------------------------------- live-route behavior
def test_followup_is_never_gated_and_never_counted(client, db):
    client.post("/analyze-screenshots")                       # first use
    for _ in range(3):
        assert client.post("/followup").status_code == 200    # Q&A on that analysis
    assert _rows(db)[0][1] == 1                               # still one use
    assert client.post("/analyze-screenshots").status_code == 403


def test_scam_check_landing_counts_as_returning(client, outbox, db):
    _confirmed_visitor(client, outbox)
    er.run_reminders(now=_now_plus(25))
    assert _rows(db)[0][4] == 1
    client.get("/scam-check")
    assert _rows(db)[0][4] == 0


def test_connect_raises_when_get_conn_returns_none(monkeypatch):
    import sys, types
    fake_app, fake_db = types.ModuleType("app"), types.ModuleType("app.db")
    fake_db.get_conn = lambda: None          # what live db.get_conn() does on failure
    fake_app.db = fake_db
    monkeypatch.setitem(sys.modules, "app", fake_app)
    monkeypatch.setitem(sys.modules, "app.db", fake_db)
    with pytest.raises(RuntimeError):
        _REAL_CONNECT()


def test_connect_returns_live_connection(monkeypatch):
    import sys, types
    sentinel = object()
    fake_app, fake_db = types.ModuleType("app"), types.ModuleType("app.db")
    fake_db.get_conn = lambda: sentinel
    fake_app.db = fake_db
    monkeypatch.setitem(sys.modules, "app", fake_app)
    monkeypatch.setitem(sys.modules, "app.db", fake_db)
    assert _REAL_CONNECT() is sentinel


def test_gate_fails_open_when_get_conn_returns_none(client, monkeypatch):
    import sys, types
    client.post("/analyze-screenshots")
    fake_app, fake_db = types.ModuleType("app"), types.ModuleType("app.db")
    fake_db.get_conn = lambda: None
    fake_app.db = fake_db
    monkeypatch.setitem(sys.modules, "app", fake_app)
    monkeypatch.setitem(sys.modules, "app.db", fake_db)
    monkeypatch.setattr(er, "_connect", _REAL_CONNECT)
    assert client.post("/analyze-screenshots").status_code == 200


# ---------------------------------------------------------------- content safety
FORBIDDEN = ["risk", "scam", "fraud", "manipul", "danger", "flag", "score", "partner",
             "dating", "ex ", "gaslight", "toxic", "abuse", "conversation", "screenshot"]


def test_reminder_and_confirm_copy_is_generic():
    for msg in (er._reminder_message("https://x/u"), er._confirm_message("https://x/c", "https://x/u")):
        blob = " ".join(msg).lower()
        for word in FORBIDDEN:
            assert word not in blob, f"generic-copy violation: {word!r}"
