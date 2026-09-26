"""
app/email_reminders.py

VibeLenz email reminder module.

Copyright © 2026 Ricky Sessums. All rights reserved.

WHAT IT DOES
  1. Issues an anonymous server-side visitor cookie (no accounts exist).
  2. After a visitor's first successful analysis, blocks the next new analysis
     (POST /analyze-screenshots -> HTTP 403, {"error": "email_required"}) until they submit an email.
  3. Sends ONE generic confirmation email. Reminders only go to confirmed,
     non-unsubscribed addresses.
  4. A daily job (run_reminders) sends one generic reminder per day to
     visitors who have been inactive 24h+, up to MAX_REMINDERS per absence.
     Any return visit resets the count (i.e. reminders stop immediately).

SAFETY DECISIONS (deliberate, do not loosen without a written reason)
  - Email content is GENERIC. No risk level, no relationship type, no
    conversation content, nothing derived from an analysis. This module never
    reads the analyses table and stores no link to it.
  - FAIL OPEN on module errors: if this module malfunctions (DB down, bad
    cookie, anything), the analysis request proceeds. A broken email feature
    must never block someone who is checking a live scam.
  - FAIL CLOSED on sending: no confirmed address, no reminder. Missing
    SendGrid config means nothing is sent.
  - Email open/click tracking is disabled on every message.

INTEGRATION (app/main.py, three lines, no endpoint bodies touched)
    from app.email_reminders import email_gate_middleware, router as email_router
    app.middleware("http")(email_gate_middleware)
    app.include_router(email_router)

DAILY JOB (Railway cron service, once per day)
    python -m app.email_reminders

ENV
    DATABASE_URL          existing Postgres URL (assumes psycopg2, see _connect)
    SENDGRID_API_KEY      required to send anything
    EMAIL_FROM_ADDRESS    must be a SendGrid-authenticated sender
    EMAIL_FROM_NAME       default "VibeLenz"
    PUBLIC_BASE_URL       default https://app.appvibelenz.com
    EMAIL_GATE_ENABLED    "false" = kill switch, gate off, cookie/tracking still on
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional, Tuple

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

log = logging.getLogger("vibelenz.email")

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
COOKIE_NAME = "vl_vid"
COOKIE_MAX_AGE = 365 * 24 * 3600
# Only new analyses are gated and counted. /followup is Q&A on an existing
# analysis (bounded by request_id); gating or counting it would lock a
# first-time visitor out of their own first result.
GATED_PATHS = {"/analyze-screenshots"}
TOUCH_PATHS = {"/", "/scam-check"}  # page loads that count as "came back"
GATE_AFTER_USES = 1          # email required from the 2nd successful analysis
MAX_REMINDERS = 7            # per absence; a return visit resets the count
REMINDER_INACTIVE_HOURS = 24
REMINDER_MIN_GAP_HOURS = 23  # cron jitter tolerance
MAX_CONFIRM_SENDS = 3        # confirmation emails per visitor, lifetime
CONFIRM_RESEND_GAP_SECONDS = 60
SEND_BATCH_LIMIT = 500

_VID_RE = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
_EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")


def _gate_enabled() -> bool:
    return os.environ.get("EMAIL_GATE_ENABLED", "true").strip().lower() != "false"


def _base_url() -> str:
    return os.environ.get("PUBLIC_BASE_URL", "https://app.appvibelenz.com").rstrip("/")


def _brand() -> str:
    return os.environ.get("EMAIL_FROM_NAME", "VibeLenz")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# DB layer. All SQL is plain, uses %s placeholders, and passes timestamps in
# from Python so it is portable and testable.
# Connection comes from app.db.get_conn() (psycopg2, verified against live db.py).
# --------------------------------------------------------------------------
def _connect():  # pragma: no cover - replaced in tests (see test_connect_*)
    # Reuse the app's existing connection code. get_conn() swallows errors and
    # returns None; turn that into an exception so every caller fails open.
    from app.db import get_conn

    conn = get_conn()
    if conn is None:
        raise RuntimeError("db unavailable")
    return conn


def _exec(sql: str, params: tuple = (), fetch: Optional[str] = None):
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = None
        if fetch == "one":
            rows = cur.fetchone()
        elif fetch == "all":
            rows = cur.fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS email_visitors (
    visitor_id            TEXT PRIMARY KEY,
    created_at            TIMESTAMPTZ NOT NULL,
    use_count             INTEGER NOT NULL DEFAULT 0,
    last_active_at        TIMESTAMPTZ NOT NULL,
    email                 TEXT,
    email_captured_at     TIMESTAMPTZ,
    confirm_token         TEXT,
    confirmed_at          TIMESTAMPTZ,
    confirm_sends         INTEGER NOT NULL DEFAULT 0,
    last_confirm_sent_at  TIMESTAMPTZ,
    unsub_token           TEXT,
    unsubscribed_at       TIMESTAMPTZ,
    reminder_count        INTEGER NOT NULL DEFAULT 0,
    last_reminder_at      TIMESTAMPTZ,
    last_send_error       TEXT
)
"""
# Deliberately NO column that references the analyses table or any analysis
# content. Reminders cannot leak what they do not know.

_schema_ready = False


def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    _exec(_SCHEMA)
    _schema_ready = True


def _valid_vid(vid: Optional[str]) -> bool:
    return bool(vid) and bool(_VID_RE.match(vid))


def normalize_email(raw: Any) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    e = raw.strip().lower()
    if len(e) > 254 or " " in e or not _EMAIL_RE.match(e):
        return None
    return e


# --------------------------------------------------------------------------
# Visitor state
# --------------------------------------------------------------------------
def gate_blocks(vid: str) -> bool:
    """True if this visitor must supply an email before another analysis."""
    ensure_schema()
    row = _exec(
        "SELECT use_count, email FROM email_visitors WHERE visitor_id = %s",
        (vid,), "one",
    )
    if not row:
        return False
    use_count, email = row
    return use_count >= GATE_AFTER_USES and not email


def record_use(vid: str) -> None:
    """A successful analysis happened. Also counts as 'returning': resets reminders."""
    ensure_schema()
    now = _utcnow()
    _exec(
        """
        INSERT INTO email_visitors (visitor_id, created_at, use_count, last_active_at)
        VALUES (%s, %s, 1, %s)
        ON CONFLICT (visitor_id) DO UPDATE SET
            use_count = email_visitors.use_count + 1,
            last_active_at = %s,
            reminder_count = 0
        """,
        (vid, now, now, now),
    )


def touch(vid: str) -> None:
    """Visitor came back (page load). Stops reminders immediately."""
    ensure_schema()
    _exec(
        "UPDATE email_visitors SET last_active_at = %s, reminder_count = 0 WHERE visitor_id = %s",
        (_utcnow(), vid),
    )


def _new_vid() -> str:
    return secrets.token_urlsafe(24)


# --------------------------------------------------------------------------
# Middleware: gate + visitor tracking. Touches no endpoint bodies.
# --------------------------------------------------------------------------
async def email_gate_middleware(request: Request, call_next):
    path = request.url.path
    gated = request.method == "POST" and path in GATED_PATHS
    raw_vid = request.cookies.get(COOKIE_NAME)
    vid = raw_vid if _valid_vid(raw_vid) else None

    # 1) Gate check (fails OPEN)
    if gated and vid and _gate_enabled():
        try:
            if await run_in_threadpool(gate_blocks, vid):
                log.info("email_gate decision=BLOCK visitor=%s path=%s", vid[:6], path)
                return JSONResponse(
                    {
                        "error": "email_required",
                        "message": "Add your email to keep going.",
                    },
                    status_code=403,
                )
        except Exception:
            log.exception("email_gate decision=FAIL_OPEN path=%s", path)

    response = await call_next(request)

    # 2) Bookkeeping after the request (never affects the response on error)
    try:
        if gated and response.status_code == 200:
            use_vid = vid or _new_vid()
            await run_in_threadpool(record_use, use_vid)
            if not vid:
                response.set_cookie(
                    COOKIE_NAME, use_vid, max_age=COOKIE_MAX_AGE,
                    httponly=True, secure=True, samesite="lax", path="/",
                )
        elif request.method == "GET" and path in TOUCH_PATHS and vid:
            await run_in_threadpool(touch, vid)
    except Exception:
        log.exception("email_gate bookkeeping_failed path=%s", path)

    return response


# --------------------------------------------------------------------------
# Sending (SendGrid v3 over stdlib urllib: no new dependency)
# --------------------------------------------------------------------------
def _send_email(
    to: str, subject: str, text: str, html: str, unsub_url: Optional[str] = None
) -> Tuple[bool, Optional[str]]:
    key = os.environ.get("SENDGRID_API_KEY")
    frm = os.environ.get("EMAIL_FROM_ADDRESS")
    if not key or not frm:
        return False, "sendgrid_not_configured"  # fail closed

    payload: dict = {
        "personalizations": [{"to": [{"email": to}]}],
        "from": {"email": frm, "name": _brand()},
        "subject": subject,
        "content": [
            {"type": "text/plain", "value": text},
            {"type": "text/html", "value": html},
        ],
        "tracking_settings": {
            "click_tracking": {"enable": False, "enable_text": False},
            "open_tracking": {"enable": False},
            "subscription_tracking": {"enable": False},
        },
    }
    if unsub_url:
        payload["headers"] = {
            "List-Unsubscribe": f"<{unsub_url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        }
    req = urllib.request.Request(
        "https://api.sendgrid.com/v3/mail/send",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return (r.status in (200, 202)), (None if r.status in (200, 202) else f"http_{r.status}")
    except urllib.error.HTTPError as e:
        return False, f"http_{e.code}"
    except Exception as e:  # network, timeout, etc.
        return False, type(e).__name__


def _confirm_message(confirm_url: str, unsub_url: str) -> Tuple[str, str, str]:
    subject = os.environ.get("EMAIL_CONFIRM_SUBJECT", "Confirm your email")
    text = (
        f"You asked {_brand()} to keep your place.\n\n"
        f"Confirm your email so we can send a short reminder now and then:\n{confirm_url}\n\n"
        f"If this wasn't you, ignore this message, or stop it here:\n{unsub_url}\n"
    )
    html = (
        f"<p>You asked {_brand()} to keep your place.</p>"
        f"<p><a href=\"{confirm_url}\">Confirm your email</a> so we can send a short reminder now and then.</p>"
        f"<p style=\"color:#666;font-size:12px\">If this wasn't you, ignore this message, "
        f"or <a href=\"{unsub_url}\">stop it here</a>.</p>"
    )
    return subject, text, html


def _reminder_message(unsub_url: str) -> Tuple[str, str, str]:
    subject = os.environ.get("EMAIL_REMINDER_SUBJECT", "Your session is waiting")
    link = _base_url()
    text = (
        f"Your {_brand()} session is still here whenever you want to come back.\n\n"
        f"{link}\n\n"
        f"Stop these reminders:\n{unsub_url}\n"
    )
    html = (
        f"<p>Your {_brand()} session is still here whenever you want to come back.</p>"
        f"<p><a href=\"{link}\">Open {_brand()}</a></p>"
        f"<p style=\"color:#666;font-size:12px\"><a href=\"{unsub_url}\">Stop these reminders</a></p>"
    )
    return subject, text, html


# --------------------------------------------------------------------------
# HTTP endpoints
# --------------------------------------------------------------------------
_TOKEN_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


def _valid_token(t) -> bool:
    # Tokens come from secrets.token_urlsafe(24): 32 URL-safe chars.
    # Whitelist before any use; the unsubscribe page reflects t into HTML.
    return isinstance(t, str) and 16 <= len(t) <= 64 and all(c in _TOKEN_CHARS for c in t)


router = APIRouter()

_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>body{{background:#0A0E14;color:#E6EDF3;font-family:system-ui,sans-serif;
display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0;padding:24px}}
.c{{max-width:420px;text-align:center}}h1{{color:#5FDCE8;font-size:1.3rem}}
button,a.b{{background:#5FDCE8;color:#0A0E14;border:0;border-radius:8px;padding:12px 20px;
font-size:1rem;cursor:pointer;text-decoration:none;display:inline-block}}</style></head>
<body><div class="c"><h1>{title}</h1>{body}</div></body></html>"""


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(_PAGE.format(title=title, body=body), status_code=status)


@router.post("/email/capture")
async def capture_email(request: Request):
    raw_vid = request.cookies.get(COOKIE_NAME)
    if not _valid_vid(raw_vid):
        return JSONResponse({"error": "no_session"}, status_code=400)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "bad_request"}, status_code=400)
    email = normalize_email(body.get("email") if isinstance(body, dict) else None)
    if not email:
        return JSONResponse({"error": "invalid_email"}, status_code=400)
    try:
        result = await run_in_threadpool(_capture, raw_vid, email)
    except Exception:
        log.exception("email_capture failed")
        return JSONResponse({"error": "temporarily_unavailable"}, status_code=503)
    if result is None:
        return JSONResponse({"error": "no_session"}, status_code=400)
    return JSONResponse({"ok": True})


def _capture(vid: str, email: str) -> Optional[bool]:
    ensure_schema()
    row = _exec(
        "SELECT email, confirm_sends, last_confirm_sent_at FROM email_visitors WHERE visitor_id = %s",
        (vid,), "one",
    )
    if not row:
        return None
    existing, confirm_sends, _last = row
    now = _utcnow()
    if existing == email:
        return True  # idempotent: gate is already satisfied, no extra send
    confirm_token = secrets.token_urlsafe(24)
    unsub_token = secrets.token_urlsafe(24)
    _exec(
        """
        UPDATE email_visitors SET email = %s, email_captured_at = %s,
            confirm_token = %s, confirmed_at = NULL, unsub_token = %s,
            unsubscribed_at = NULL, reminder_count = 0, last_reminder_at = NULL
        WHERE visitor_id = %s
        """,
        (email, now, confirm_token, unsub_token, vid),
    )
    # Cap confirmation sends (email-bombing guard) then send.
    if confirm_sends < MAX_CONFIRM_SENDS:
        cutoff = now - timedelta(seconds=CONFIRM_RESEND_GAP_SECONDS)
        claimed = _exec(
            """
            UPDATE email_visitors SET confirm_sends = confirm_sends + 1, last_confirm_sent_at = %s
            WHERE visitor_id = %s AND confirm_sends < %s
              AND (last_confirm_sent_at IS NULL OR last_confirm_sent_at <= %s)
            RETURNING visitor_id
            """,
            (now, vid, MAX_CONFIRM_SENDS, cutoff), "one",
        )
        if claimed:
            base = _base_url()
            subject, text, html = _confirm_message(
                f"{base}/email/confirm?t={confirm_token}",
                f"{base}/email/unsubscribe?t={unsub_token}",
            )
            ok, err = _send_email(email, subject, text, html,
                                  unsub_url=f"{base}/email/unsubscribe?t={unsub_token}")
            log.info("email_confirm_send visitor=%s ok=%s err=%s", vid[:6], ok, err)
            if not ok:
                _exec("UPDATE email_visitors SET last_send_error = %s WHERE visitor_id = %s", (err, vid))
    return True


@router.get("/email/confirm")
async def confirm_email(t: str = ""):
    if not _valid_token(t):
        return _page("Link not valid", "<p>This link isn't valid anymore.</p>", 400)
    try:
        row = await run_in_threadpool(
            _exec,
            "UPDATE email_visitors SET confirmed_at = %s WHERE confirm_token = %s "
            "AND unsubscribed_at IS NULL RETURNING visitor_id",
            (_utcnow(), t), "one",
        )
    except Exception:
        log.exception("email_confirm failed")
        return _page("Try again later", "<p>Something went wrong. Please try the link again in a bit.</p>", 503)
    if not row:
        return _page("Link not valid", "<p>This link isn't valid anymore.</p>", 400)
    return _page("You're set", "<p>Thanks. We'll send a short reminder now and then.</p>"
                 f"<p><a class=\"b\" href=\"{_base_url()}\">Back to {_brand()}</a></p>")


@router.get("/email/unsubscribe")
async def unsubscribe_page(t: str = ""):
    # GET only shows a button. Mail scanners prefetch links; a GET that
    # unsubscribes would silently cancel people who never clicked.
    if not _valid_token(t):
        return _page("Link not valid", "<p>This link isn't valid anymore.</p>", 400)
    return _page(
        "Stop reminders?",
        f"<form method=\"post\" action=\"/email/unsubscribe?t={t}\">"
        "<p>You won't get any more emails from us.</p>"
        "<button type=\"submit\">Yes, stop them</button></form>",
    )


@router.post("/email/unsubscribe")
async def unsubscribe_do(request: Request):
    # Serves both the button above and RFC 8058 one-click (token in query string).
    t = request.query_params.get("t", "")
    if not _valid_token(t):
        return _page("Link not valid", "<p>This link isn't valid anymore.</p>", 400)
    try:
        row = await run_in_threadpool(
            _exec,
            "UPDATE email_visitors SET unsubscribed_at = %s WHERE unsub_token = %s RETURNING visitor_id",
            (_utcnow(), t), "one",
        )
    except Exception:
        log.exception("email_unsubscribe failed")
        return _page("Try again later", "<p>Something went wrong. Please try again in a bit.</p>", 503)
    if not row:
        return _page("Link not valid", "<p>This link isn't valid anymore.</p>", 400)
    log.info("email_unsubscribe visitor=%s", row[0][:6])
    return _page("Done", "<p>No more reminders. Take care.</p>")


# --------------------------------------------------------------------------
# Daily job
# --------------------------------------------------------------------------
def run_reminders(
    now: Optional[datetime] = None,
    limit: int = SEND_BATCH_LIMIT,
    sender: Optional[Callable[..., Tuple[bool, Optional[str]]]] = None,
) -> dict:
    sender = sender or _send_email  # resolved at call time, not definition time
    ensure_schema()
    now = now or _utcnow()
    inactive_cut = now - timedelta(hours=REMINDER_INACTIVE_HOURS)
    gap_cut = now - timedelta(hours=REMINDER_MIN_GAP_HOURS)
    rows = _exec(
        """
        SELECT visitor_id, email, unsub_token FROM email_visitors
        WHERE confirmed_at IS NOT NULL AND unsubscribed_at IS NULL AND email IS NOT NULL
          AND reminder_count < %s AND last_active_at <= %s
          AND (last_reminder_at IS NULL OR last_reminder_at <= %s)
        ORDER BY last_active_at ASC LIMIT %s
        """,
        (MAX_REMINDERS, inactive_cut, gap_cut, limit), "all",
    ) or []
    sent = failed = skipped = 0
    for vid, email, unsub_token in rows:
        # Claim BEFORE sending: a crash after send can never double-send.
        claimed = _exec(
            """
            UPDATE email_visitors SET reminder_count = reminder_count + 1, last_reminder_at = %s
            WHERE visitor_id = %s AND unsubscribed_at IS NULL AND confirmed_at IS NOT NULL
              AND reminder_count < %s AND last_active_at <= %s
              AND (last_reminder_at IS NULL OR last_reminder_at <= %s)
            RETURNING visitor_id
            """,
            (now, vid, MAX_REMINDERS, inactive_cut, gap_cut), "one",
        )
        if not claimed:
            skipped += 1
            continue
        unsub_url = f"{_base_url()}/email/unsubscribe?t={unsub_token}"
        subject, text, html = _reminder_message(unsub_url)
        ok, err = sender(email, subject, text, html, unsub_url=unsub_url)
        if ok:
            sent += 1
        else:
            failed += 1
            _exec("UPDATE email_visitors SET last_send_error = %s WHERE visitor_id = %s", (err, vid))
        log.info("email_reminder visitor=%s ok=%s err=%s", vid[:6], ok, err)
    summary = {"eligible": len(rows), "sent": sent, "failed": failed, "skipped": skipped}
    log.info("email_reminder_run %s", json.dumps(summary))
    return summary


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(run_reminders()))
