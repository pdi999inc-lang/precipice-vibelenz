"""
audit.py - VibeLenz Structured Audit Logger
VIE Audit Trail v1.0

Every analysis produces a structured audit record containing:
- request_id, timestamp, processing_time_ms
- ocr_char_count, image_count
- risk_score, lane, vie_action, confidence
- signals with evidence
- active_combos
- degraded state

Records are written to:
1. Railway log stream (structured JSON — machine readable)
2. /tmp/vibelenz_audit_{session_id}.jsonl (one record per line, persists for container session)
"""

from __future__ import annotations

import json
import logging
import re
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger("vibelenz.audit")

# Keys permitted in the [AUDIT] LOG LINE. The full record still goes to the audit
# file and to the caller untouched; this governs only what reaches the platform log
# stream, whose retention we do not control.
#
# An ALLOWLIST, not a denylist, deliberately: result["evidence"] maps signal ids to
# "<exact quote from conversation>" (see the schema in app/analyzer_combined.py), so
# a field carrying user text must never reach the log by default. With a denylist,
# any key added to the record later would leak until someone remembered to exclude
# it. Adding a key here is a conscious act.
_LOG_SAFE_KEYS = (
    "event", "session_id", "request_id", "timestamp", "processing_time_ms",
    "image_count", "ocr_char_count", "risk_score", "lane", "vie_action",
    "confidence", "signal_count", "signals", "active_combos", "degraded",
)
# Only an exception CLASS name may be logged, never the message. Truncating the
# message does not work: an LLM parse failure reads
#   "ValueError: unparseable model output near <the user's sentence>"
# so the quote lands inside the first 200 characters. Caught by
# tests/test_audit_log_redaction.py before this shipped.
#
# A class name has no spaces and ends in a known suffix, so prose cannot satisfy
# it. Anything else is reported as "unclassified" and the real message stays in the
# returned record, the audit file and Postgres.
_ERROR_CLASS_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.]{0,60}(?:Error|Exception|Warning|Timeout)$")


def _error_class(err: Any) -> str:
    head = str(err).split(":", 1)[0].strip()
    return head if _ERROR_CLASS_RE.match(head) else "unclassified"

def _log_projection(record: Dict[str, Any]) -> Dict[str, Any]:
    """The log-safe view of an audit record.

    Signal ids and counts are kept: they are the diagnostic value and carry no
    conversation content. The quotes behind them are dropped - evidence becomes a
    list of WHICH signals had evidence, never what it said.
    """
    out = {k: record[k] for k in _LOG_SAFE_KEYS if k in record}
    ev = record.get("evidence")
    if isinstance(ev, dict):
        out["evidence_signals"] = sorted(str(k) for k in ev.keys())
        out["evidence_count"] = len(ev)
    elif ev:
        # Unexpected shape: say so, show nothing.
        out["evidence_count"] = -1
    # The FLAG-tier injection signal must stay auditable - a soft match is a security
    # event even though it never changes a read. But analyzer_combined sets it to
    #     {"tier": "soft", "matched": <the matched conversation text>}
    # so only the tier may be logged. Dropping the key entirely broke
    # test_signal_reaches_the_audit_log_but_not_the_client, which is correct to
    # demand it; logging it whole would have leaked the fragment while that test
    # still passed. Tier only satisfies both.
    inj = record.get("injection_suspected")
    if isinstance(inj, dict):
        out["injection_suspected"] = {"tier": str(inj.get("tier", "unknown"))}
    elif inj is not None:
        out["injection_suspected"] = {"tier": "unknown"}
    err = record.get("error")
    if err:
        # Class name only. The message can echo the model input verbatim.
        out["error_class"] = _error_class(err)
    return out

SESSION_ID = str(uuid.uuid4())[:8]
AUDIT_FILE = f"/tmp/vibelenz_audit_{SESSION_ID}.jsonl"


def _init_session() -> None:
    try:
        with open(AUDIT_FILE, "a") as f:
            record = {
                "event": "SESSION_START",
                "session_id": SESSION_ID,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "pid": os.getpid(),
            }
            f.write(json.dumps(record) + "\n")
        logger.info(f"[AUDIT] Session {SESSION_ID} started. Audit file: {AUDIT_FILE}")
    except Exception as e:
        logger.warning(f"[AUDIT] Could not initialize audit file: {e}")


_init_session()


def write_audit_record(
    request_id: str,
    timestamp_start: float,
    image_count: int,
    ocr_char_count: int,
    result: Dict[str, Any],
    degraded: bool = False,
    error: Optional[str] = None,
    vie_t: Optional[Dict[str, Any]] = None,
    # Retained after the Gottman module moved to its own repo (2026-11-22) so
    # existing callers and replayed records keep working. VibeLenz no longer
    # passes it; records written before the move keep their "gottman" key.
    gottman: Optional[Dict[str, Any]] = None,
    consistency: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Write a structured audit record for a single analysis request.
    Returns the audit record dict.
    """
    processing_time_ms = round((time.time() - timestamp_start) * 1000)

    record = {
        "event": "ANALYSIS",
        "session_id": SESSION_ID,
        "request_id": request_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "processing_time_ms": processing_time_ms,
        "image_count": image_count,
        "ocr_char_count": ocr_char_count,
        "risk_score": result.get("risk_score", 0),
        "lane": result.get("lane", "NONE"),           # fixed: was "phase"
        "vie_action": result.get("vie_action", "NONE"),
        "confidence": result.get("confidence", 0.0),
        "signal_count": len([
            f for f in result.get("flags", [])
            if f != "No signals detected"
        ]),
        "signals": result.get("flags", []),
        "evidence": result.get("evidence", {}),
        "active_combos": result.get("active_combos", []),
        "degraded": degraded,
        "error": error,
    }
    # Shadow-mode in-conversation trajectory (app/vie_t_adapter.py). Audit-only:
    # additive key, absent when not computed, never affects the decision above.
    if vie_t is not None:
        record["vie_t"] = vie_t
    if gottman is not None:
        record["gottman"] = gottman
    if consistency is not None:
        record["consistency"] = consistency
    # FLAG-tier injection signal (analyzer_combined._check_injection_soft). Audit-only,
    # additive key, absent when nothing matched; never affects the decision above.
    if isinstance(result, dict) and result.get("injection_suspected") is not None:
        record["injection_suspected"] = result.get("injection_suspected")

    # Redacted projection only - see _log_projection and _LOG_SAFE_KEYS. The full
    # record is returned to the caller and written to the audit file below.
    logger.info(f"[AUDIT] {json.dumps(_log_projection(record))}")

    try:
        with open(AUDIT_FILE, "a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as e:
        logger.warning(f"[AUDIT] Could not write to audit file: {e}")

    return record


def get_session_stats() -> Dict[str, Any]:
    """Read audit file and return session-level stats."""
    try:
        records = []
        with open(AUDIT_FILE, "r") as f:
            for line in f:
                try:
                    r = json.loads(line.strip())
                    if r.get("event") == "ANALYSIS":
                        records.append(r)
                except Exception:
                    continue

        if not records:
            return {"session_id": SESSION_ID, "total_analyses": 0}

        scores = [r["risk_score"] for r in records]
        return {
            "session_id": SESSION_ID,
            "total_analyses": len(records),
            "avg_risk_score": round(sum(scores) / len(scores), 1),
            "high_risk_count":   sum(1 for s in scores if s >= 70),
            "medium_risk_count": sum(1 for s in scores if 40 <= s < 70),
            "low_risk_count":    sum(1 for s in scores if s < 40),
            "degraded_count":    sum(1 for r in records if r.get("degraded")),
            "audit_file": AUDIT_FILE,
        }
    except Exception as e:
        logger.warning(f"[AUDIT] Could not read session stats: {e}")
        return {"session_id": SESSION_ID, "error": str(e)}
