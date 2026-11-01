"""
consistency_gate.py — cross-section consistency verifier for the result page. SHADOW MODE.

Copyright (c) 2026 Ricky Sessums. All rights reserved.

WHY
  Result-page sections are computed independently (deterministic chips, the
  narrative LLM call, the arc, replies). Nothing checked that they agree, so a
  read could say "Effort goes both ways" next to "you're doing most of the work".

MVP SCOPE (deterministic rules only)
  R1 effort_contradiction    narrative says effort is one-sided, page shows the
                             "Effort goes both ways" chip (reciprocal_engagement)
  R2 watch_unverified        the narrative call named things to watch but none
                             survived quote verification (likely invented)
  R3 alt_reads_missing       connection read with no verified alternative read
  R4 nothing_off_contradicts the page would say "Nothing here stands out" while the
                             narrative call named at least one thing to watch

MODES
  CONSISTENCY_SHADOW_ENABLED (default on):  compute + audit-log violations only.
  CONSISTENCY_ENFORCE (default off):        also set `mixed_signals_note` on the
                                            payload and drop the contradicted chip.
  Enforce stays off until the shadow logs show the rules fire correctly.

DEGRADED BEHAVIOR
  Any error -> None. The gate never blocks a read and never changes lane,
  risk_score, flags or the narrative text.

AUDIT
  `violations` is a list of rule ids; no conversation text is logged.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "cgate.v1"

MIXED_SIGNALS_NOTE = (
    "Heads up: parts of this read point different ways. The written take above is "
    "based on the full conversation, so lean on that over any single chip."
)


def _flag(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def shadow_enabled() -> bool:
    return _flag("CONSISTENCY_SHADOW_ENABLED", True)


def enforce_enabled() -> bool:
    return _flag("CONSISTENCY_ENFORCE", False)


def _shows_effort_chip(payload: Dict[str, Any]) -> bool:
    hp = payload.get("healthy_patterns") or {}
    for c in hp.get("chips") or []:
        if isinstance(c, dict) and c.get("key") == "reciprocal_engagement":
            return True
    return "reciprocal_engagement" in (payload.get("positive_signals") or [])


def _det_concerns(payload: Dict[str, Any]) -> List[str]:
    return [c for c in (payload.get("concern_signals") or []) if c and c != "No signals detected"]


def check_consistency(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Pure. Reads the payload, never mutates it. Returns None when not applicable."""
    try:
        if not isinstance(payload, dict):
            return None
        if str(payload.get("presentation_mode", "")).lower() != "connection":
            return None
        ctx = payload.get("context_reads")
        if not ctx:
            return None
        v: List[str] = []
        effort = ctx.get("effort_balance")
        if effort in {"you_more", "them_more"} and _shows_effort_chip(payload):
            v.append("effort_contradiction")
        if ctx.get("watch_raw_count", 0) > 0 and not ctx.get("watch"):
            v.append("watch_unverified")
        if not ctx.get("alt_reads"):
            v.append("alt_reads_missing")
        if ctx.get("watch_raw_count", 0) > 0 and not ctx.get("watch") and not _det_concerns(payload):
            v.append("nothing_off_contradicts")
        return {"schema": SCHEMA_VERSION, "violations": v, "enforced": False}
    except Exception:  # noqa: BLE001
        return None


def apply_enforcement(payload: Dict[str, Any], result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Only called when CONSISTENCY_ENFORCE is on. Suppress-only: removes the
    contradicted chip and adds the note. Never adds positive content."""
    if not result or not result.get("violations"):
        return payload
    v = set(result["violations"])
    if "effort_contradiction" in v:
        hp = payload.get("healthy_patterns")
        if isinstance(hp, dict):
            hp = dict(hp)
            hp["chips"] = [c for c in hp.get("chips") or [] if not (isinstance(c, dict) and c.get("key") == "reciprocal_engagement")]
            payload["healthy_patterns"] = hp
        payload["positive_signals"] = [s for s in payload.get("positive_signals") or [] if s != "reciprocal_engagement"]
    if v & {"effort_contradiction", "nothing_off_contradicts"}:
        payload["mixed_signals_note"] = MIXED_SIGNALS_NOTE
    result["enforced"] = True
    return payload
