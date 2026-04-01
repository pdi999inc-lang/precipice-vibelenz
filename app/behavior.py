"""
behavior.py - VibeLenz deterministic behavioral pre-filter.

Role in pipeline:
    OCR → behavior.py (this file) → FLAML verifier (stub) → AnalysisResponse

Purpose:
    Extract measurable behavioral signals from conversation turns before
    ML inference. Produces a BehaviorProfile that feeds into the verifier
    as supplementary features.

Design constraints:
    - No ML, no embeddings, no sentiment models
    - All signals are deterministic and reproducible
    - Fail-closed: any extraction error returns a zeroed profile, not an exception
    - All scores normalized 0.0–1.0 for FLAML feature compatibility
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional

logger = logging.getLogger("vibelenz.behavior")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class BehaviorProfile:
    """
    Normalized behavioral signal scores for a conversation.
    All float fields are in range [0.0, 1.0] unless noted.
    Feeds directly into FLAML verifier as feature vector.
    """

    # Reciprocity: balance of effort between participants
    reciprocity_score: float = 0.0          # 1.0 = balanced, 0.0 = fully one-sided

    # Initiative: who drives the conversation
    initiative_score: float = 0.0           # 1.0 = other party drives, 0.0 = passive

    # Engagement depth: message length and density
    engagement_depth_score: float = 0.0     # 1.0 = high effort messages

    # Continuity: references to past context
    continuity_score: float = 0.0           # 1.0 = strong memory/callbacks

    # Forward movement: progression toward plans or actions
    forward_movement_score: float = 0.0     # 1.0 = actively moving toward something

    # Pressure signals: urgency, financial asks, isolation attempts
    pressure_score: float = 0.0             # 1.0 = high pressure detected (risk signal)

    # Composite risk flag (deterministic gate, not ML)
    # True if pressure_score >= PRESSURE_THRESHOLD AND engagement is asymmetric
    deterministic_flag: bool = False

    # Raw counts (not normalized — passed as-is to verifier)
    total_turns: int = 0
    other_questions: int = 0
    financial_mentions: int = 0
    urgency_mentions: int = 0
    isolation_mentions: int = 0

    def to_feature_vector(self) -> Dict[str, float]:
        """
        Flat dict for FLAML feature input.
        Boolean converted to float. Counts included as-is.
        """
        return {
            "reciprocity_score": self.reciprocity_score,
            "initiative_score": self.initiative_score,
            "engagement_depth_score": self.engagement_depth_score,
            "continuity_score": self.continuity_score,
            "forward_movement_score": self.forward_movement_score,
            "pressure_score": self.pressure_score,
            "deterministic_flag": float(self.deterministic_flag),
            "total_turns": float(self.total_turns),
            "other_questions": float(self.other_questions),
            "financial_mentions": float(self.financial_mentions),
            "urgency_mentions": float(self.urgency_mentions),
            "isolation_mentions": float(self.isolation_mentions),
        }

    def to_dict(self) -> dict:
        """API-safe serialization."""
        return asdict(self)


# ---------------------------------------------------------------------------
# Signal patterns
# ---------------------------------------------------------------------------

_FINANCIAL_PATTERNS = re.compile(
    r"""
    \$\d+                           # dollar amount
    | \b(send|transfer|wire|venmo|cashapp|zelle|paypal)\b   # transfer verbs
    | \b(gift\s*card|bitcoin|crypto|western\s*union)\b      # high-risk instruments
    | \binvest(ment|ing)?\b         # investment language
    """,
    re.IGNORECASE | re.VERBOSE,
)

_URGENCY_PATTERNS = re.compile(
    r"""
    \b(urgent|urgently|asap|immediately|right\s*now|hurry|emergency)\b
    | \b(limited\s*time|act\s*now|don't\s*wait|last\s*chance)\b
    | \b(today\s*only|expires?\s*(soon|today))\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

_ISOLATION_PATTERNS = re.compile(
    r"""
    \b(don't\s*tell|keep\s*(this|it|us)\s*(secret|between\s*us))\b
    | \b(no\s*one\s*(needs\s*to\s*know|else\s*needs))\b
    | \b(just\s*(between|us|you\s*and\s*me))\b
    | \b(stay\s*away\s*from|avoid\s*(your|them|friends|family))\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

_QUESTION_PATTERN = re.compile(r"\?")

_CALLBACK_PATTERN = re.compile(
    r"\b(remember|you\s*said|last\s*time|earlier|as\s*we\s*(discussed|talked))\b",
    re.IGNORECASE,
)

_FORWARD_PATTERN = re.compile(
    r"\b(let's|let\s*us|plan|schedule|meet|when\s*can|next\s*time|soon)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

PRESSURE_THRESHOLD = 0.4        # pressure_score at or above this → risk signal
ASYMMETRY_THRESHOLD = 0.35      # reciprocity below this → asymmetric conversation
AVG_LENGTH_HIGH = 15            # words per message considered "high effort"
AVG_LENGTH_LOW = 4              # words per message considered "low effort"


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------

class BehaviorExtractor:
    """
    Extracts deterministic behavioral signals from conversation turns.

    Expected turn format:
        {"turn_id": "T1", "sender": "user"|"other", "text": "..."}

    "other" = the party being analyzed (potential bad actor or match quality signal)
    "user"  = the VibeLenz user (the one seeking protection/insight)
    """

    def extract(self, turn_history: List[Dict]) -> BehaviorProfile:
        """
        Main extraction entry point.
        Fail-closed: returns zeroed BehaviorProfile on any error.
        """
        if not turn_history:
            logger.warning("BehaviorExtractor: empty turn history")
            return BehaviorProfile()

        try:
            return self._extract(turn_history)
        except Exception as e:
            logger.error(f"BehaviorExtractor: extraction failed — {e}")
            return BehaviorProfile()  # fail-closed

    def _extract(self, turns: List[Dict]) -> BehaviorProfile:
        user_turns = [t for t in turns if t.get("sender") == "user"]
        other_turns = [t for t in turns if t.get("sender") == "other"]

        total = len(turns)
        n_user = len(user_turns)
        n_other = len(other_turns)

        # --- Reciprocity ---
        if total > 0:
            ratio = abs(n_user - n_other) / total
            reciprocity = max(0.0, 1.0 - ratio)
        else:
            reciprocity = 0.0

        # --- Engagement depth (other party only) ---
        if other_turns:
            word_counts = [len(t.get("text", "").split()) for t in other_turns]
            avg_words = sum(word_counts) / len(word_counts)
            if avg_words >= AVG_LENGTH_HIGH:
                engagement = 1.0
            elif avg_words <= AVG_LENGTH_LOW:
                engagement = 0.0
            else:
                engagement = (avg_words - AVG_LENGTH_LOW) / (AVG_LENGTH_HIGH - AVG_LENGTH_LOW)
        else:
            engagement = 0.0

        # --- Initiative (other party) ---
        other_questions = sum(
            len(_QUESTION_PATTERN.findall(t.get("text", ""))) for t in other_turns
        )
        # Normalize against total turns to prevent inflation on long conversations
        initiative = min(1.0, other_questions / max(total, 1))

        # --- Continuity ---
        callback_count = sum(
            1 for t in other_turns
            if _CALLBACK_PATTERN.search(t.get("text", ""))
        )
        continuity = min(1.0, callback_count / max(n_other, 1))

        # --- Forward movement ---
        forward_count = sum(
            1 for t in other_turns
            if _FORWARD_PATTERN.search(t.get("text", ""))
        )
        forward = min(1.0, forward_count / max(n_other, 1))

        # --- Pressure signals ---
        all_other_text = " ".join(t.get("text", "") for t in other_turns)

        financial_hits = len(_FINANCIAL_PATTERNS.findall(all_other_text))
        urgency_hits = len(_URGENCY_PATTERNS.findall(all_other_text))
        isolation_hits = len(_ISOLATION_PATTERNS.findall(all_other_text))

        raw_pressure = financial_hits + urgency_hits + (isolation_hits * 2)  # isolation weighted
        pressure = min(1.0, raw_pressure / 10.0)  # normalize: 10+ signals = 1.0

        # --- Deterministic flag ---
        # Hard gate: elevated pressure AND asymmetric conversation
        det_flag = (
            pressure >= PRESSURE_THRESHOLD
            and reciprocity <= ASYMMETRY_THRESHOLD
        )

        profile = BehaviorProfile(
            reciprocity_score=round(reciprocity, 3),
            initiative_score=round(initiative, 3),
            engagement_depth_score=round(engagement, 3),
            continuity_score=round(continuity, 3),
            forward_movement_score=round(forward, 3),
            pressure_score=round(pressure, 3),
            deterministic_flag=det_flag,
            total_turns=total,
            other_questions=other_questions,
            financial_mentions=financial_hits,
            urgency_mentions=urgency_hits,
            isolation_mentions=isolation_hits,
        )

        logger.info(
            f"BehaviorExtractor: {total} turns | pressure={profile.pressure_score} | "
            f"flag={profile.deterministic_flag}"
        )

        return profile
