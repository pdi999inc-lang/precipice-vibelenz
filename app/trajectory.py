"""
app/trajectory.py

VibeLenz cross-session trajectory — "compared to last time."

Copyright © 2026 Ricky Sessums. All rights reserved.

WHAT IT DOES
  build_trajectory() turns the frozen per-batch scores already stored by
  app/db.save_batch() into one plain-language read of how a conversation has
  moved across separate visits. Pure function over payload["prior_batches"].

WHAT IT IS NOT
  Not a new engine. No new scoring, no new model, no new table. Every number
  it reports was computed and frozen by the analyzer at the time of that read.
  This module only compares and describes.

IN-CONVERSATION vs CROSS-SESSION
  In-conversation movement (arc across screenshots inside ONE upload) already
  exists as turn_analysis.arc_label in app/analyzer_combined.py. This module
  is strictly the cross-session layer: batch N vs batches 1..N-1.

SAFETY DECISIONS (deliberate, do not loosen without a written reason)
  - Direction of travel is safety-asymmetric. Movement toward risk is always
    reported. Movement away from risk is reported as easing, never as "safe" or
    "resolved" — a conversation that de-escalated is not a conversation that
    was harmless.
  - A single elevated batch is never smoothed away by later calm ones. If any
    prior batch was HIGH or CRITICAL, the summary says so even when the current
    read is low. peak_risk_level exists for exactly this reason.
  - Voice splits by lane, per the product voice rule: connection mode reads
    like a friend, risk mode stays clinical.
  - Needs >= 2 batches. One batch is not a trajectory; returns None.
  - Degrades to None whenever continuity is degraded or the stored data is
    malformed. It never guesses at movement it cannot see.
  - Identity is the existing conversation_id (browser localStorage, 30-day
    device-local continuity). This module adds NO cookie read, NO link to
    email identity, and NO new retention surface.
  - Pure: no DB, no network, no clock. Safe to call anywhere.

INTEGRATION (app/main.py, after prior_batches is attached)
    from app.trajectory import build_trajectory
    payload["trajectory"] = build_trajectory(payload)   # inside try/except
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "traj.v1"

CONNECTION = "connection"
RISK = "risk"

MIN_BATCHES = 2
MAX_POINTS = 12          # cap the sparkline; older batches fold into the first point
MATERIAL_DELTA = 10      # below this, movement is noise, not a trend

_LEVEL_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3, "WITHHELD": 3}
_ELEVATED = {"HIGH", "CRITICAL", "WITHHELD"}


def _level_rank(level: Any) -> int:
    return _LEVEL_RANK.get(str(level or "").strip().upper(), 0)


def _coerce_batch(row: Any) -> Optional[Dict[str, Any]]:
    """Accept only a well-formed frozen batch row. No guessing."""
    if not isinstance(row, dict):
        return None
    try:
        n = int(row.get("batch_number"))
        score = int(row.get("risk_score"))
    except (TypeError, ValueError):
        return None
    if n < 1 or not (0 <= score <= 100):
        return None
    return {
        "batch_number": n,
        "risk_score": score,
        "risk_level": str(row.get("risk_level") or "").strip().upper(),
        "created_at": row.get("created_at"),
    }


def _direction(delta: int) -> str:
    if delta >= MATERIAL_DELTA:
        return "toward_risk"
    if delta <= -MATERIAL_DELTA:
        return "easing"
    return "steady"


def _summary(voice: str, direction: str, delta: int, peak_elevated: bool, reads: int) -> str:
    """One plain sentence. Safety-asymmetric: easing is never framed as resolved."""
    mag = abs(delta)
    if voice == CONNECTION:
        base = {
            "toward_risk": "Since your last read, this has moved in a direction worth paying attention to.",
            "easing": "Since your last read, things have settled down a little.",
            "steady": "This is holding about where it was last time.",
        }[direction]
        if peak_elevated and direction != "toward_risk":
            base += " One of your earlier reads did stand out though, and that hasn't stopped being true."
        return base
    base = {
        "toward_risk": f"Risk has increased {mag} points since the previous read.",
        "easing": f"Risk has decreased {mag} points since the previous read.",
        "steady": "Risk is materially unchanged since the previous read.",
    }[direction]
    if peak_elevated and direction != "toward_risk":
        base += " An earlier read in this conversation reached an elevated level; that assessment still stands."
    if reads >= 4 and direction == "toward_risk":
        base += " This is a sustained pattern, not a single reading."
    return base


def build_trajectory(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Pure and deterministic. Returns the cross-session movement for this
    conversation, or None when there is no trajectory to report.
    """
    if not isinstance(payload, dict):
        return None
    if payload.get("continuity_degraded"):
        return None

    rows = payload.get("prior_batches")
    if not isinstance(rows, (list, tuple)):
        return None

    batches: List[Dict[str, Any]] = []
    seen = set()
    for r in rows:
        b = _coerce_batch(r)
        if b and b["batch_number"] not in seen:
            seen.add(b["batch_number"])
            batches.append(b)
    batches.sort(key=lambda b: b["batch_number"])
    if len(batches) < MIN_BATCHES:
        return None

    current, previous = batches[-1], batches[-2]
    delta = current["risk_score"] - previous["risk_score"]
    first_delta = current["risk_score"] - batches[0]["risk_score"]

    peak = max(batches, key=lambda b: (_level_rank(b["risk_level"]), b["risk_score"]))
    peak_elevated = peak["risk_level"] in _ELEVATED

    voice = CONNECTION if str(payload.get("presentation_mode") or "") == CONNECTION else RISK
    direction = _direction(delta)

    points = [
        {"n": b["batch_number"], "score": b["risk_score"], "level": b["risk_level"]}
        for b in batches[-MAX_POINTS:]
    ]

    return {
        "version": SCHEMA_VERSION,
        "voice": voice,
        "reads": len(batches),
        "direction": direction,
        "delta": delta,
        "delta_since_first": first_delta,
        "current_score": current["risk_score"],
        "previous_score": previous["risk_score"],
        "peak_score": peak["risk_score"],
        "peak_risk_level": peak["risk_level"],
        "peak_batch": peak["batch_number"],
        "peak_elevated": peak_elevated,
        "points": points,
        "summary": _summary(voice, direction, delta, peak_elevated, len(batches)),
    }
