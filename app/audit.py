"""
audit.py - VibeLenz Structured Audit Logger
VIE Audit Trail v1.0

Every analysis produces a structured audit record containing:
- request_id, timestamp, processing_time_ms
- ocr_char_count, image_count
- risk_score, phase, vie_action, confidence
- signals with evidence
- active_combos
- degraded state

Records are written to:
1. Railway log stream (structured JSON — machine readable)
2. /tmp/vibelenz_audit_{session_id}.jsonl (one record per line, persists for container session)
"""

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vibelenz.audit")

# Session ID — unique per container boot
SESSION_ID = str(uuid.uuid4())[:8]
AUDIT_FILE = f"/tmp/vibelenz_audit_{SESSION_ID}.jsonl"

# Write session start marker
def _init_session():
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
        "phase": result.get("phase", "NONE"),
        "vie_action": result.get("vie_action", "NONE"),
        "confidence": result.get("confidence", 0.0),
        "signal_count": len([f for f in result.get("flags", []) if f != "No signals detected"]),
        "signals": result.get("flags", []),
        "evidence": result.get("evidence", {}),
        "active_combos": result.get("active_combos", []),
        "degraded": degraded,
        "error": error,
    }

    # 1. Structured log to Railway
    logger.info(f"[AUDIT] {json.dumps(record)}")

    # 2. Write to session audit file
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
            "high_risk_count": sum(1 for s in scores if s >= 70),
            "medium_risk_count": sum(1 for s in scores if 40 <= s < 70),
            "low_risk_count": sum(1 for s in scores if s < 40),
            "degraded_count": sum(1 for r in records if r.get("degraded")),
            "audit_file": AUDIT_FILE,
        }
    except Exception as e:
        logger.warning(f"[AUDIT] Could not read session stats: {e}")
        return {"session_id": SESSION_ID, "error": str(e)}
