"""
app/literacy.py

VibeLenz pattern literacy check ("What stood out to you?").

Copyright © 2026 Ricky Sessums. All rights reserved.

WHAT IT DOES
  1. build_prompt(): pure, deterministic. Given a finished analysis payload,
     returns a small multiple-choice prompt (what the read picked up, mixed
     with plausible options it did not) or None when the read is ineligible.
  2. POST /literacy/answer: stores the user's picks as a training label.
     Covered by the front-door training notice (notice-only consent, no
     checkbox) — index.html tells users their analyzed chats help improve
     the product. This module stores strictly less than that notice covers:
     option keys only, never conversation text.

SAFETY DECISIONS (deliberate, do not loosen without a written reason)
  - Connection mode only. Never shown for FRAUD / COERCION_RISK lanes, never
    for degraded reads, never for withheld reads. Eligibility is re-checked
    server-side from the stored analysis row; the client is not trusted.
  - Stores NO conversation text and NO visitor/email identity. A label row is
    request_id + lane + option keys + picks. Nothing links it to vl_vid.
    Nothing here can re-identify a person or reconstruct a conversation.
  - Deterministic: options are a pure function of (request_id, positive
    signals). The server recomputes them on submit and rejects any pick that
    was not on the card.
  - FAIL CLOSED on storage: DB missing, row missing, or any error -> nothing
    stored, request still returns cleanly. This feature can never block or
    alter an analysis.
  - Every decision emits one structured log line (audit trail):
        literacy decision=<STORED|DUPLICATE|REFUSED|ERROR> reason=<...>

INTEGRATION (app/main.py)
    from app.literacy import router as literacy_router, build_prompt as build_literacy_prompt
    app.include_router(literacy_router)
    payload["literacy"] = build_literacy_prompt(payload)   # inside try/except

KNOWN LIMITATION
  The green-flag chips render on the same page. Users who scroll first can
  copy the answer, which biases labels toward agreement. The card is placed
  above the chips to reduce this; treat labels as "noticed or agreed", not
  as independent perception, until a layout change hides chips pre-answer.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

log = logging.getLogger("vibelenz.literacy")

SCHEMA_VERSION = "lit.v1"
ELIGIBLE_MODE = "connection"
BLOCKED_LANES = {"FRAUD", "COERCION_RISK"}
MAX_DETECTED_OPTIONS = 3
TOTAL_OPTIONS = 5
MIN_DISTRACTORS = 2

_SIGNAL_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_RID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")

# Plausible things a good conversation can have. Used as distractors when the
# read did not pick them up. Keys match analyzer vocabulary where it exists.
DISTRACTOR_BANK: Sequence[str] = (
    "reciprocal_engagement",
    "warm_reception_present",
    "playful_engagement_present",
    "repair_attempt_present",
    "mutual_curiosity",
    "future_planning",
    "consistent_follow_through",
    "respects_boundaries",
)

# Connection-mode voice: a thoughtful friend, not a scanner.
FRIENDLY_LABELS: Dict[str, str] = {
    "reciprocal_engagement": "You're both putting in effort",
    "warm_reception_present": "They're warm when you reach out",
    "playful_engagement_present": "There's playful back-and-forth",
    "sexual_reciprocity_present": "The flirting goes both ways",
    "repair_attempt_present": "A bumpy moment got smoothed over",
    "initial_confusion_present": "It started out a little confusing",
    "mutual_curiosity": "You're both asking about each other",
    "future_planning": "Plans or the future came up",
    "consistent_follow_through": "They follow through on what they say",
    "respects_boundaries": "Boundaries get respected",
    "no_financial_topics": "Money never comes up",
}

QUESTION = "Before you read on: what stood out to you?"
SUBTEXT = "Pick anything you noticed. Then see what this read picked up."


def friendly_label(key: str) -> str:
    if key in FRIENDLY_LABELS:
        return FRIENDLY_LABELS[key]
    base = key[:-8] if key.endswith("_present") else key
    return base.replace("_", " ").strip().capitalize()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _order_key(request_id: str, name: str) -> str:
    return hashlib.sha256(f"{request_id}|{name}".encode("utf-8")).hexdigest()


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return []
    return list(value) if isinstance(value, (list, tuple)) else []


def eligibility(lane: Any, presentation_mode: Any, degraded: Any) -> Optional[str]:
    """Return None if eligible, else a short refusal reason."""
    if str(lane or "").upper() in BLOCKED_LANES:
        return "blocked_lane"
    if str(presentation_mode or "") != ELIGIBLE_MODE:
        return "not_connection_mode"
    if bool(degraded):
        return "degraded"
    return None


def build_prompt(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Pure and deterministic. Returns None when the read is ineligible or has
    nothing to reveal. Never raises for ordinary malformed input.
    """
    if not isinstance(payload, dict):
        return None
    request_id = str(payload.get("request_id") or "")
    if not _RID_RE.match(request_id):
        return None
    if eligibility(payload.get("lane"), payload.get("presentation_mode"), payload.get("degraded")):
        return None
    # Mirrors result.html: risk_text = (risk_level or risk_label)
    risk_text = str(payload.get("risk_level") or payload.get("risk_label") or "").upper()
    if risk_text == "WITHHELD":
        return None

    detected: List[str] = []
    for s in _as_list(payload.get("positive_signals")):
        if isinstance(s, str) and _SIGNAL_RE.match(s) and s not in detected:
            detected.append(s)
    detected = detected[:MAX_DETECTED_OPTIONS]
    if not detected:
        return None

    pool = [d for d in DISTRACTOR_BANK if d not in detected]
    pool.sort(key=lambda n: _order_key(request_id, n))
    n_distractors = max(MIN_DISTRACTORS, TOTAL_OPTIONS - len(detected))
    distractors = pool[:n_distractors]

    keys = detected + distractors
    keys.sort(key=lambda n: _order_key(request_id, "opt:" + n))
    return {
        "version": SCHEMA_VERSION,
        "question": QUESTION,
        "subtext": SUBTEXT,
        "options": [{"key": k, "label": friendly_label(k)} for k in keys],
        "detected": list(detected),
    }


# --------------------------------------------------------------------------
# DB layer (same pattern as email_reminders: plain SQL, %s placeholders,
# timestamps passed from Python, JSON stored as TEXT for portability).
# --------------------------------------------------------------------------
def _connect():  # pragma: no cover - replaced in tests
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
CREATE TABLE IF NOT EXISTS literacy_labels (
    request_id      TEXT PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL,
    schema_version  TEXT NOT NULL,
    lane            TEXT,
    options         TEXT NOT NULL,
    detected        TEXT NOT NULL,
    selected        TEXT NOT NULL,
    hit_count       INTEGER NOT NULL,
    missed_count    INTEGER NOT NULL,
    extra_count     INTEGER NOT NULL
)
"""
# Deliberately NO visitor_id, NO email, NO conversation text.

_schema_ready = False


def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    _exec(_SCHEMA)
    _schema_ready = True


def _load_analysis(request_id: str):
    return _exec(
        "SELECT lane, presentation_mode, degraded, positive_signals, risk_level "
        "FROM analyses WHERE request_id = %s",
        (request_id,), "one",
    )


def store_answer(request_id: str, selected: List[str]) -> Dict[str, Any]:
    """
    Recompute the card from the stored analysis, validate picks, insert once.
    Returns {"decision": ..., "reason": ...}. Raises only on DB failure.
    """
    row = _load_analysis(request_id)
    if not row:
        return {"decision": "REFUSED", "reason": "not_found"}
    lane, presentation_mode, degraded, positive_signals, risk_level = row
    prompt = build_prompt({
        "request_id": request_id,
        "lane": lane,
        "presentation_mode": presentation_mode,
        "degraded": degraded,
        "positive_signals": positive_signals,
        "risk_level": risk_level,
    })
    if prompt is None:
        return {"decision": "REFUSED", "reason": "ineligible"}
    option_keys = [o["key"] for o in prompt["options"]]
    if any(s not in option_keys for s in selected):
        return {"decision": "REFUSED", "reason": "pick_not_on_card"}

    detected = set(prompt["detected"])
    picked = set(selected)
    ensure_schema()
    inserted = _exec(
        """
        INSERT INTO literacy_labels (request_id, created_at, schema_version, lane,
            options, detected, selected, hit_count, missed_count, extra_count)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (request_id) DO NOTHING
        RETURNING request_id
        """,
        (
            request_id, _utcnow(), SCHEMA_VERSION, str(lane or ""),
            json.dumps(option_keys), json.dumps(sorted(detected)), json.dumps(sorted(picked)),
            len(picked & detected), len(detected - picked), len(picked - detected),
        ),
        "one",
    )
    if not inserted:
        return {"decision": "DUPLICATE", "reason": "already_answered"}
    return {"decision": "STORED", "reason": "ok"}


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
router = APIRouter()


def _audit(decision: str, reason: str, request_id: str = "") -> None:
    log.info("literacy decision=%s reason=%s request=%s", decision, reason, (request_id or "-")[:12])


@router.post("/literacy/answer")
async def literacy_answer(request: Request):
    try:
        body = await request.json()
    except Exception:
        _audit("REFUSED", "bad_json")
        return JSONResponse({"stored": False, "reason": "bad_request"}, status_code=400)
    if not isinstance(body, dict):
        _audit("REFUSED", "bad_body")
        return JSONResponse({"stored": False, "reason": "bad_request"}, status_code=400)

    request_id = str(body.get("request_id") or "")
    if not _RID_RE.match(request_id):
        _audit("REFUSED", "bad_request_id")
        return JSONResponse({"stored": False, "reason": "bad_request"}, status_code=400)
    selected = body.get("selected")
    if (
        not isinstance(selected, list)
        or not selected
        or len(selected) > TOTAL_OPTIONS
        or not all(isinstance(s, str) and _SIGNAL_RE.match(s) for s in selected)
    ):
        _audit("REFUSED", "bad_selection", request_id)
        return JSONResponse({"stored": False, "reason": "bad_selection"}, status_code=422)
    selected = list(dict.fromkeys(selected))

    try:
        result = await run_in_threadpool(store_answer, request_id, selected)
    except Exception:
        log.exception("literacy decision=ERROR reason=storage_failed request=%s", request_id[:12])
        return JSONResponse({"stored": False, "reason": "temporarily_unavailable"}, status_code=503)

    _audit(result["decision"], result["reason"], request_id)
    if result["decision"] == "STORED":
        return JSONResponse({"stored": True})
    if result["decision"] == "DUPLICATE":
        return JSONResponse({"stored": False, "reason": result["reason"]})
    status = {"not_found": 404, "ineligible": 403, "pick_not_on_card": 422}.get(result["reason"], 400)
    return JSONResponse({"stored": False, "reason": result["reason"]}, status_code=status)
