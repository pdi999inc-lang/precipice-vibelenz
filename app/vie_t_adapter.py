"""
app/vie_t_adapter.py - bridge from live analyzer output to VIE-T, in SHADOW MODE.

Copyright © 2026 Ricky Sessums. All rights reserved.

run_shadow() takes the speaker-labeled conversation text (YOU:/THEM: lines, as
produced by OCR or pasted by the user), analyzes each message with the live
DETERMINISTIC engine (no LLM, no DB, no network), feeds the sequence to
VIE-T, and returns a compact dict for the audit record.

SHADOW-MODE CONTRACT (do not loosen without a written reason)
  - Output goes to the audit record ONLY. It is never added to the user
    payload, never changes a risk score, lane, or action, and can never lower
    or clear a risk decision.
  - run_shadow() NEVER raises. Any failure returns status ERROR. An advisory
    layer must not be able to block a read.
  - Fail closed on thin evidence: no speaker labels -> SKIPPED_NO_SPEAKER (a
    reciprocity metric with no speakers would be fabricated, not measured).

KNOWN BIASES (recorded, not hidden)
  - PER_MESSAGE_LANE_SKEW: the analyzer's lane logic wants multi-signal
    clusters, so single messages skew BENIGN and per-message risk_score runs
    lower than the conversation-level score.
  - Per-message risk_level must NOT be compared with the conversation-level
    score. Risk acceleration is a difference of means, so a constant downward
    bias cancels and the delta stays usable.
  - OCR text carries no timestamps, so every live trajectory carries the
    MISSING_TIMESTAMPS ceiling (0.90).
  - Unlabeled continuation lines inherit the previous speaker and count as
    separate messages.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from app.vie_t import TurnObservation, compute_trajectory
from app.vie_t_taxonomy import LIVE_TAXONOMY, reconcile

ADAPTER_VERSION = "0.4.0"
MAX_TURNS = 80          # most recent messages considered; bounds cost
MIN_TURNS = 2           # below this there is nothing to compare
EVENT_TURN_ID_CAP = 5   # evidence IDs kept per event in the audit projection

AnalyzeFn = Callable[[str, str], Dict[str, Any]]


def _default_analyze(line: str, relationship_type: str) -> Dict[str, Any]:
    # Imported lazily so this module stays importable (and testable) without
    # pulling the whole analyzer, and so a failed import degrades to ERROR.
    from app.analyzer_combined import _apply_physical_harm_override, _run_deterministic

    return _apply_physical_harm_override(_run_deterministic(line, relationship_type), line)


def _signals_of(analysis: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for key in ("key_signals", "concern_signals", "flags", "key_dampeners", "positive_signals"):
        for s in analysis.get(key) or []:
            if s and s != "No signals detected":
                out.append(str(s))
    return list(dict.fromkeys(out))


def run_shadow(
    text: str,
    relationship_type: str = "stranger",
    analyze: Optional[AnalyzeFn] = None,
) -> Dict[str, Any]:
    """Return the audit-record projection. Never raises."""
    t0 = time.perf_counter()
    base: Dict[str, Any] = {"adapter_version": ADAPTER_VERSION}
    try:
        from app.subtle_patterns import parse_turns

        parsed, labeled = parse_turns(text or "")
        labeled_turns = [(spk, line) for spk, line in parsed if spk in ("you", "them")]

        if not labeled or not labeled_turns:
            return {**base, "vie_t_status": "SKIPPED_NO_SPEAKER"}
        if len(labeled_turns) < MIN_TURNS:
            return {**base, "vie_t_status": "SKIPPED_TOO_FEW_TURNS",
                    "turns_observed": len(labeled_turns)}

        labeled_turns = labeled_turns[-MAX_TURNS:]
        fn = analyze or _default_analyze

        observations: List[TurnObservation] = []
        emitted: List[str] = []
        failures = 0
        for i, (speaker, line) in enumerate(labeled_turns):
            try:
                analysis = fn(line, relationship_type) or {}
            except Exception:  # one bad message must not sink the sequence
                analysis = {}
                failures += 1
            signals = _signals_of(analysis)
            emitted.extend(signals)
            observations.append(
                TurnObservation(
                    turn_id=f"T{i}",
                    speaker=speaker,
                    text_len=len(line),
                    question_count=line.count("?"),
                    risk_score=float(analysis.get("risk_score", 0) or 0),
                    signals=signals,
                    safety_critical=bool(
                        analysis.get("vie_action") == "BLOCK"
                        or "physical_harm_disclosed" in signals
                    ),
                    timestamp=None,
                )
            )

        # Fail closed: if the analyzer failed on EVERY message, the zeros are not a
        # calm conversation, they are missing data. Say so instead of reporting it.
        if failures == len(observations):
            return {**base, "vie_t_status": "ERROR", "vie_t_error": "ANALYZER_UNAVAILABLE",
                    "analyzer_failures": failures, "turns_observed": len(observations)}

        result = compute_trajectory(observations, taxonomy=LIVE_TAXONOMY)
        fields = result.to_audit_fields()
        fields["trajectory_event_turns"] = {
            e.event: e.turn_ids[:EVENT_TURN_ID_CAP] for e in result.events
        }
        return {
            **base,
            "vie_t_status": "OK",
            **fields,
            "analyzer_failures": failures,
            "unmapped_signals": reconcile(emitted)["unmapped"],
            "shadow_ms": round((time.perf_counter() - t0) * 1000, 1),
        }
    except Exception as e:  # noqa: BLE001 - shadow mode must never raise
        return {**base, "vie_t_status": "ERROR", "vie_t_error": f"{type(e).__name__}: {e}"[:200]}
