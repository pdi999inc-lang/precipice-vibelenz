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
"""

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("vibelenz.degradation")


class DegradationState(Enum):
    NOMINAL = "NOMINAL"
    SOFT_DEGRADED = "SOFT_DEGRADED"
    HARD_DEGRADED = "HARD_DEGRADED"
    FAIL_CLOSED = "FAIL_CLOSED"


@dataclass
class DegradationAssessment:
    state: DegradationState
    reasons: list
    should_block: bool
    confidence_penalty: float  # 0.0 = no penalty, 1.0 = zero confidence


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
    reasons = []
    state = DegradationState.NOMINAL
    confidence_penalty = 0.0

    # FAIL_CLOSED triggers
    if result_degraded and api_error:
        reasons.append(f"API error: {api_error}")
        return DegradationAssessment(
            state=DegradationState.FAIL_CLOSED,
            reasons=reasons,
            should_block=True,
            confidence_penalty=1.0,
        )

    # HARD_DEGRADED triggers
    if ocr_char_count < MIN_OCR_CHARS:
        reasons.append(f"OCR extracted only {ocr_char_count} chars — image quality too low")
        state = DegradationState.HARD_DEGRADED
        confidence_penalty = max(confidence_penalty, 0.5)

    if confidence < MIN_CONFIDENCE:
        reasons.append(f"Analysis confidence {confidence:.0%} below minimum threshold")
        state = DegradationState.HARD_DEGRADED
        confidence_penalty = max(confidence_penalty, 0.6)

    if processing_time_ms > HARD_PROCESSING_MS:
        reasons.append(f"Processing time {processing_time_ms}ms exceeded hard limit")
        state = DegradationState.HARD_DEGRADED
        confidence_penalty = max(confidence_penalty, 0.3)

    # SOFT_DEGRADED triggers (only if not already HARD)
    if state == DegradationState.NOMINAL:
        if confidence < SOFT_CONFIDENCE:
            reasons.append(f"Analysis confidence {confidence:.0%} below optimal threshold")
            state = DegradationState.SOFT_DEGRADED
            confidence_penalty = max(confidence_penalty, 0.2)

        if processing_time_ms > MAX_PROCESSING_MS:
            reasons.append(f"Processing time {processing_time_ms}ms above optimal")
            state = DegradationState.SOFT_DEGRADED
            confidence_penalty = max(confidence_penalty, 0.1)

        if result_degraded:
            reasons.append("Analysis engine reported degraded state")
            state = DegradationState.SOFT_DEGRADED
            confidence_penalty = max(confidence_penalty, 0.3)

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
    """
    result["degradation_state"] = assessment.state.value
    result["degradation_reasons"] = assessment.reasons

    if assessment.confidence_penalty > 0:
        original_confidence = result.get("confidence", 0.5)
        result["confidence"] = max(0.0, original_confidence - assessment.confidence_penalty)

    if assessment.state == DegradationState.HARD_DEGRADED:
        result["degraded"] = True
        logger.warning(
            f"[DEGRADATION] HARD_DEGRADED — reasons: {assessment.reasons}"
        )

    elif assessment.state == DegradationState.SOFT_DEGRADED:
        logger.warning(
            f"[DEGRADATION] SOFT_DEGRADED — reasons: {assessment.reasons}"
        )

    elif assessment.state == DegradationState.NOMINAL and assessment.reasons:
        logger.info(
            f"[DEGRADATION] NOMINAL with notes: {assessment.reasons}"
        )

    return result
