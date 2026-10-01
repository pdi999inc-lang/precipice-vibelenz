"""
outcome_scoring.py - which follow-up answers fit which prediction, and how each
answer scores against it.
Copyright © 2026 Ricky Sessums. All rights reserved.

Pure functions, no I/O. Used by the /outcome endpoint (to reject answers that
do not fit the open prediction) and by scripts/score_outcomes.py (offline
accuracy). Accuracy stays internal; nothing here is shown to users.

Scores: "hit" (prediction held), "miss" (it did not), "unscorable" (the answer
cannot confirm or refute it). Safety predictions get their own answer set:
a warm option is never offered after a fraud or coercion read.
"""
from __future__ import annotations

from typing import Dict, Optional

SCORING: Dict[str, Dict[str, str]] = {
    # FRAUD / COERCION_RISK: predicted "pressure_or_ask_continues".
    "escalation": {
        "pressure_continued": "hit",
        "pressure_stopped": "miss",
        "no_contact": "unscorable",
    },
    # Predicted "reply_or_warmth_continues".
    "engagement": {
        "warmed_up": "hit",
        "lukewarm": "unscorable",
        "went_quiet": "miss",
    },
    # Predicted "contact_slows_or_stops".
    "fade": {
        "warmed_up": "miss",
        "lukewarm": "hit",
        "went_quiet": "hit",
    },
    # "no_major_shift" is too vague to falsify with these answers. Collected
    # for capture-rate purposes only; never counted toward accuracy.
    "steady": {
        "warmed_up": "unscorable",
        "lukewarm": "unscorable",
        "went_quiet": "unscorable",
    },
}


def allowed_outcomes(prediction_type: Optional[str]) -> set:
    """Answers that fit this prediction type. Unknown or missing type -> none."""
    return set(SCORING.get(str(prediction_type or ""), {}))


def score(prediction_type: Optional[str], outcome: Optional[str]) -> str:
    """hit / miss / unscorable. Anything mismatched is unscorable, never a hit."""
    return SCORING.get(str(prediction_type or ""), {}).get(str(outcome or ""), "unscorable")
