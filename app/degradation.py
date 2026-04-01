"""
degradation.py - VibeLenz Formal Degradation Engine
VIE Degradation Framework v1.0

States:
- NOMINAL: All systems operational, full analysis
- SOFT_DEGRADED: Minor issues (slow response, low confidence), analysis runs with warning
- HARD_DEGRADED: Significant issues, analysis runs with reduced reliability
- FAIL_CLOSED: Complete block, no output, HTTP 503

Triggers:
- OCR char count too low (likely bad image quality)
- Analysis confidence below threshold
- Processing time exceeded threshold
- Exception caught in analysis pipeline
- Empty or truncated response from Claude API

FIXES APPLIED
-------------
[B1] FAIL_CLOSED gate changed from (result_degraded AND api_error) to (api_error alone).
     An API error before any result is returned — the most common failure mode — previously
     never triggered FAIL_CLOSED. result_degraded is now a secondary soft trigger, not
     required to gate on the error path.

[B2] apply_degradation now handles FAIL_CLOSED state explicitly: sets degraded=True,
     caps risk_score to 100, zeroes confidence, and logs at ERROR level. Previously
     FAIL_CLOSED fell through all branches and returned a partially valid result.

[B3] Removed unused imports: time, Tuple.

[D1] apply_degradation now preserves original_confidence in the result dict so
     downstream callers can distinguish a penalized-high-confidence result from a
     genuinely low-confidence one.

[D2] confidence_penalty now compounds additively across simultaneous hard triggers
     (capped at 1.0) rather than just taking the max. Three simultaneous hard failures
     produce a larger penalty than one, consistent with VIE's safety-native model.

[T1] DegradationAssessment.reasons typed as List[str] instead of bare list.

[T2] assess_degradation guards against negative ocr_char_count and processing_time_ms
     inputs — upstream sentinel values like -1 no longer silently bypass OCR checks.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vibelenz.degradation")


class DegradationState(Enum):
    NOMINAL = "NOMINAL"
    SOFT_DEGRADED = "SOFT_DEGRADED"
    HARD_DEGRADED = "HARD_DEGRADED"
    FAIL_CLOSED = "FAIL_CLOSED"


@dataclass
class DegradationAssessment:
    state: DegradationState
    reasons: List[str]          # [T1] typed as List[str]
    should_block: bool
    confidence_penalty: float   # 0.0 = no penalty, 1.0 = zero confidence


# Thresholds
MIN_OCR_CHARS = 50           # Below this = likely unreadable image
MIN_CONFIDENCE = 0.15        # Below this = hard degraded
SOFT_CONFIDENCE = 0.35       # Below this = soft degraded
MAX_PROCESSING_MS = 30000    # 30 seconds = soft degraded
HARD_PROCESSING_MS = 60000   # 60 seconds = hard degraded


def assess_degradation(
    ocr_char_count: int,
    confidence: float,
    processing_time_ms: int,
    api_error: Optional[str] = None,
    result_degraded: bool = False,
) -> DegradationAssessment:
    """
    Assess system degradation state based on observable metrics.
    Returns a DegradationAssessment with state, reasons, and penalties.
    """
    reasons: List[str] = []
    state = DegradationState.NOMINAL
    confidence_penalty = 0.0

    # [T2] Clamp negative sentinel inputs so they don't bypass checks
    ocr_char_count = max(0, ocr_char_count)
    processing_time_ms = max(0, processing_time_ms)

    # [B1] FAIL_CLOSED: api_error alone is sufficient — result_degraded is no longer required.
    # An API error before any result is returned is the most common hard failure mode.
    if api_error:
        reasons.append(f"API error: {api_error}")
        if result_degraded:
            reasons.append("Analysis engine also reported degraded state")
        return DegradationAssessment(
            state=DegradationState.FAIL_CLOSED,
            reasons=reasons,
            should_block=True,
            confidence_penalty=1.0,
        )

    # HARD_DEGRADED triggers
    # [D2] Penalties accumulate additively (capped at 1.0) across simultaneous triggers
    if ocr_char_count < MIN_OCR_CHARS:
        reasons.append(f"OCR extracted only {ocr_char_count} chars — image quality too low")
        state = DegradationState.HARD_DEGRADED
        confidence_penalty = min(1.0, confidence_penalty + 0.5)

    if confidence < MIN_CONFIDENCE:
        reasons.append(f"Analysis confidence {confidence:.0%} below minimum threshold")
        state = DegradationState.HARD_DEGRADED
        confidence_penalty = min(1.0, confidence_penalty + 0.6)

    if processing_time_ms > HARD_PROCESSING_MS:
        reasons.append(f"Processing time {processing_time_ms}ms exceeded hard limit")
        state = DegradationState.HARD_DEGRADED
        confidence_penalty = min(1.0, confidence_penalty + 0.3)

    # SOFT_DEGRADED triggers (only if not already HARD)
    if state == DegradationState.NOMINAL:
        if confidence < SOFT_CONFIDENCE:
            reasons.append(f"Analysis confidence {confidence:.0%} below optimal threshold")
            state = DegradationState.SOFT_DEGRADED
            confidence_penalty = min(1.0, confidence_penalty + 0.2)

        if processing_time_ms > MAX_PROCESSING_MS:
            reasons.append(f"Processing time {processing_time_ms}ms above optimal")
            state = DegradationState.SOFT_DEGRADED
            confidence_penalty = min(1.0, confidence_penalty + 0.1)

        if result_degraded:
            reasons.append("Analysis engine reported degraded state")
            state = DegradationState.SOFT_DEGRADED
            confidence_penalty = min(1.0, confidence_penalty + 0.3)

    return DegradationAssessment(
        state=state,
        reasons=reasons,
        should_block=False,
        confidence_penalty=confidence_penalty,
    )


def apply_degradation(
    result: Dict[str, Any],
    assessment: DegradationAssessment,
) -> Dict[str, Any]:
    """
    Apply degradation penalties to the analysis result.
    Adjusts confidence, adds degradation metadata.
    Does NOT modify risk_score — that remains deterministic.
    Exception: FAIL_CLOSED sets risk_score=100 as a safety-native hard block.
    """
    result["degradation_state"] = assessment.state.value
    result["degradation_reasons"] = assessment.reasons

    # [D1] Preserve original confidence before applying penalty
    original_confidence = float(result.get("confidence", 0.5))
    result["original_confidence"] = original_confidence

    if assessment.confidence_penalty > 0:
        result["confidence"] = max(0.0, original_confidence - assessment.confidence_penalty)

    # [B2] FAIL_CLOSED now handled explicitly — enforces hard block contract
    if assessment.state == DegradationState.FAIL_CLOSED:
        result["degraded"] = True
        result["confidence"] = 0.0
        result["risk_score"] = 100   # safety-native: unknown = maximum caution
        result["vie_action"] = "BLOCK"
        logger.error(
            "[DEGRADATION] FAIL_CLOSED — blocking output. Reasons: %s",
            assessment.reasons,
        )

    elif assessment.state == DegradationState.HARD_DEGRADED:
        result["degraded"] = True
        logger.warning(
            "[DEGRADATION] HARD_DEGRADED — reasons: %s", assessment.reasons,
        )

    elif assessment.state == DegradationState.SOFT_DEGRADED:
        logger.warning(
            "[DEGRADATION] SOFT_DEGRADED — reasons: %s", assessment.reasons,
        )

    elif assessment.state == DegradationState.NOMINAL and assessment.reasons:
        logger.info(
            "[DEGRADATION] NOMINAL with notes: %s", assessment.reasons,
        )

    return result
