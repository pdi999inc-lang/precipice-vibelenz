"""
context_reads.py — "Worth watching" and "Other ways to read it", grounded in the actual chat.

Copyright (c) 2026 Ricky Sessums. All rights reserved.

WHY
  Both sections used to render the same text on almost every read:
    - Worth watching: deterministic concern_signals only fire on a narrow set of
      risk phrases, so connection reads showed "Nothing here stands out as off".
    - Other ways to read it: a static list per lane (deterministic path) or an
      empty list (LLM path -> "None surfaced").

WHAT
  The existing connection-mode enrichment call (interpreter._llm_enrich) now also
  returns `watch_items`, `alt_reads` and `effort_balance` in the same JSON. No new
  LLM call, no new dependency. This module is the safety filter between that raw
  output and the page (invariant 3: no LLM output exposed without filtering).

SAFETY FILTER (deterministic, fail-closed)
  An item renders only if ALL of these hold:
    1. connection presentation mode, not a risk-override lane, not degraded,
       not withheld, risk_score < 60
    2. its `quote` appears verbatim in the extracted text (whitespace, case and
       curly-quote normalized) -> no invented evidence
    3. no clinical/scanner vocabulary (voice rule: that is reserved for risk mode)
    4. length caps; max 3 items per section; duplicates dropped
  Anything that fails is dropped and counted. Any exception -> None (no card).

DEGRADED BEHAVIOR
  No API key / LLM error / nothing verified -> the section is hidden. The template
  still shows deterministic concern chips in "Worth watching" when present.

AUDIT
  `audit_view()` returns counts only (no quotes, no conversation text).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "ctx.v1"
MAX_ITEMS = 3
MAX_POINT = 220
MIN_QUOTE = 3
MAX_QUOTE = 160
EFFORT_VALUES = {"you_more", "them_more", "even", "unclear"}

_RISK_LANES = {"FRAUD", "COERCION_RISK", "BLOCKED"}

# Scanner vocabulary is reserved for risk mode (VibeLenz UX voice rule).
_BANNED = re.compile(
    r"\b(?:risk\w*|danger\w*|threat\w*|red[\s-]?flags?|flag(?:s|ged)?|signals?|pressure|"
    r"toxic|narcissis\w*|manipulat\w*|predator\w*|abus\w*|gaslight\w*|scam\w*)\b",
    re.I,
)

_QUOTE_CHARS = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "…": "..."})
_SPEAKER_TAG = re.compile(r"(?im)^\s*(?:you|them|left|right)\s*:\s*")


def _norm(s: str) -> str:
    s = str(s or "").translate(_QUOTE_CHARS)
    s = _SPEAKER_TAG.sub("", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s.strip(" \"'")


def _eligible(payload: Dict[str, Any]) -> bool:
    if str(payload.get("presentation_mode", "")).lower() != "connection":
        return False
    if str(payload.get("lane", "")).upper() in _RISK_LANES:
        return False
    if payload.get("degraded") or payload.get("is_withheld") or payload.get("physical_harm_override"):
        return False
    try:
        if int(payload.get("risk_score", 0) or 0) >= 60:
            return False
    except (TypeError, ValueError):
        return False
    return True


def _speaker(v: Any) -> str:
    v = str(v or "").strip().lower()
    return v if v in {"you", "them"} else "chat"


def _validate(items: Any, haystack: str, text_key: str) -> (List[Dict[str, str]], int):
    """Return (kept, dropped_count). Each kept item: {text, quote, speaker}."""
    kept: List[Dict[str, str]] = []
    dropped = 0
    seen = set()
    if not isinstance(items, list):
        return [], (1 if items else 0)
    for it in items:
        if not isinstance(it, dict):
            dropped += 1
            continue
        text = re.sub(r"\s+", " ", str(it.get(text_key) or "")).strip()
        quote = re.sub(r"\s+", " ", str(it.get("quote") or "")).strip().strip("\"'“”")
        nq = _norm(quote)
        ok = (
            text
            and len(text) <= MAX_POINT
            and MIN_QUOTE <= len(nq) <= MAX_QUOTE
            and nq in haystack
            and not _BANNED.search(text)
            and _norm(text) not in seen
        )
        if not ok:
            dropped += 1
            continue
        seen.add(_norm(text))
        kept.append({"text": text, "quote": quote, "speaker": _speaker(it.get("speaker"))})
        if len(kept) >= MAX_ITEMS:
            break
    return kept, dropped


def build_context_reads(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Pure + deterministic. Never raises (outer callers also guard)."""
    try:
        if not isinstance(payload, dict) or not _eligible(payload):
            return None
        haystack = _norm(payload.get("extracted_text", ""))
        if not haystack:
            return None
        watch, w_drop = _validate(payload.get("watch_items_raw"), haystack, "point")
        alts, a_drop = _validate(payload.get("alt_reads_raw"), haystack, "read")
        effort = str(payload.get("effort_balance_raw") or "unclear").strip().lower()
        if effort not in EFFORT_VALUES:
            effort = "unclear"
        return {
            "schema": SCHEMA_VERSION,
            "watch": watch,
            "alt_reads": alts,
            "effort_balance": effort,
            "watch_dropped": w_drop,
            "alt_dropped": a_drop,
            "watch_raw_count": len(payload.get("watch_items_raw") or []) if isinstance(payload.get("watch_items_raw"), list) else 0,
            "alt_raw_count": len(payload.get("alt_reads_raw") or []) if isinstance(payload.get("alt_reads_raw"), list) else 0,
        }
    except Exception:  # noqa: BLE001 — fail closed: no card, never a blocked read
        return None


def audit_view(ctx: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Counts only. No quotes or conversation text in the audit log."""
    if not ctx:
        return None
    return {
        "schema": ctx.get("schema"),
        "watch_kept": len(ctx.get("watch") or []),
        "watch_dropped": ctx.get("watch_dropped", 0),
        "alt_kept": len(ctx.get("alt_reads") or []),
        "alt_dropped": ctx.get("alt_dropped", 0),
        "effort_balance": ctx.get("effort_balance"),
    }
