"""
behavior.py — VibeLenz Safety / Deterministic Layer

Pipeline position:
    OCR → turn parser → behavior.py (THIS) → BehaviorProfile
                      → relationship_dynamics.py
                      ↓ (both feed into)
                    analyzer_combined → interpreter → AnalysisResponse

Design:
    - Consumes verified turn format: {"turn_id": str, "sender": "user"|"other", "text": str}
    - Produces BehaviorProfile — safety/deterministic signal extraction
    - No ML, no embeddings — pure pattern matching
    - Fail-closed: empty input returns zeroed BehaviorProfile (does not raise)
    - deterministic_flag = pressure_score >= PRESSURE_THRESHOLD
                           AND reciprocity_score <= ASYMMETRY_THRESHOLD

Public interface:
    BehaviorExtractor().extract(turns: List[Dict]) -> BehaviorProfile
    analyze_behavior(turns) -> Dict   ← called by api.py via asyncio.to_thread
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vibelenz.behavior")

# ---------------------------------------------------------------------------
# Thresholds (exported — benchmark imports these)
# ---------------------------------------------------------------------------

PRESSURE_THRESHOLD: float = 0.35   # pressure_score >= this → potential flag
ASYMMETRY_THRESHOLD: float = 0.25  # reciprocity_score <= this → asymmetric


# ---------------------------------------------------------------------------
# Signal phrase lists
# ---------------------------------------------------------------------------

_FINANCIAL_TERMS = [
    "send", "wire", "transfer", "pay", "venmo", "cashapp", "zelle", "paypal",
    "bitcoin", "crypto", "deposit", "$", "money", "cash", "funds", "payment",
    "invest", "investment", "wallet", "bank account", "routing", "western union",
    "moneygram", "gift card",
]

_URGENCY_TERMS = [
    "urgent", "urgently", "immediately", "right now", "asap", "act now",
    "limited time", "today only", "deadline", "expires", "don't wait",
    "do not wait", "hurry", "quickly", "time sensitive", "last chance",
    "before it's too late", "before it is too late", "within 24 hours",
    "within the hour", "must act",
]

_ISOLATION_TERMS = [
    "don't tell", "do not tell", "keep this between us", "keep it between us",
    "don't mention", "do not mention", "don't share", "do not share",
    "our secret", "just between us", "no one needs to know",
    "don't tell anyone", "do not tell anyone", "tell no one",
    "keep this private", "don't involve", "do not involve",
]

_PRESSURE_PHRASES = [
    "you have to", "you must", "you need to", "there is no other way",
    "there's no other way", "only option", "last chance", "or else",
    "if you don't", "if you do not", "trust me on this",
]

_QUESTION_RE = re.compile(r"\?")
_WORD_RE = re.compile(r"\b\w+\b")


# ---------------------------------------------------------------------------
# BehaviorProfile
# ---------------------------------------------------------------------------

@dataclass
class BehaviorProfile:
    """
    Output of BehaviorExtractor.extract(). All float scores in [0.0, 1.0].

    Fields match the BehaviorResult schema contract in schemas.py.
    """
    # Turn-level engagement scores
    reciprocity_score: float = 0.0       # balance of turns between user/other
    initiative_score: float = 0.0        # how often "other" initiates topics
    engagement_depth_score: float = 0.0  # avg message length normalised
    continuity_score: float = 0.0        # topic callbacks / references
    forward_movement_score: float = 0.0  # future-oriented language

    # Safety scores
    pressure_score: float = 0.0          # urgency + financial + isolation combined
    isolation_score: float = 0.0         # isolation signal density
    urgency_score: float = 0.0           # urgency signal density
    asymmetry_score: float = 0.0         # turn-count asymmetry from "other"

    # Counts (raw, not normalised — useful for debugging)
    financial_mentions: int = 0
    urgency_mentions: int = 0
    isolation_mentions: int = 0
    pressure_phrase_hits: int = 0

    # Hard gate
    deterministic_flag: bool = False      # True when pressure + asymmetry cross thresholds

    # Metadata
    turn_count: int = 0
    other_turn_count: int = 0
    user_turn_count: int = 0
    degraded: bool = False

    def to_feature_vector(self) -> Dict[str, Any]:
        """
        Flat dict of all normalised float scores for downstream consumers
        (FLAML verifier, analyzer_combined, schemas.BehaviorResult).
        """
        return {
            "reciprocity_score":       self.reciprocity_score,
            "initiative_score":        self.initiative_score,
            "engagement_depth_score":  self.engagement_depth_score,
            "continuity_score":        self.continuity_score,
            "forward_movement_score":  self.forward_movement_score,
            "pressure_score":          self.pressure_score,
            "isolation_score":         self.isolation_score,
            "urgency_score":           self.urgency_score,
            "asymmetry_score":         self.asymmetry_score,
            "financial_mentions":      self.financial_mentions,
            "urgency_mentions":        self.urgency_mentions,
            "isolation_mentions":      self.isolation_mentions,
            "deterministic_flag":      self.deterministic_flag,
            "turn_count":              self.turn_count,
        }

    def to_schema_dict(self) -> Dict[str, Any]:
        """
        Maps to BehaviorResult Pydantic field names for AnalysisResponse.
        risk_score here = pressure_score (the primary safety signal).
        confidence = 1.0 - degraded penalty.
        """
        flags = self._active_flags()
        return {
            "risk_score":         round(self.pressure_score, 4),
            "flags":              flags,
            "confidence":         0.5 if self.degraded else round(
                                      min(1.0, 0.55 + len(flags) * 0.07), 2
                                  ),
            "degraded":           self.degraded,
            "pressure_score":     round(self.pressure_score, 4),
            "isolation_score":    round(self.isolation_score, 4),
            "urgency_score":      round(self.urgency_score, 4),
            "asymmetry_score":    round(self.asymmetry_score, 4),
            "deterministic_flag": self.deterministic_flag,
        }

    def _active_flags(self) -> List[str]:
        flags = []
        if self.pressure_score > 0.0:
            flags.append("pressure_present")
        if self.urgency_score > 0.3:
            flags.append("urgency_detected")
        if self.isolation_score > 0.3:
            flags.append("isolation_detected")
        if self.financial_mentions > 0:
            flags.append("financial_mention")
        if self.deterministic_flag:
            flags.append("deterministic_gate_triggered")
        return flags


# ---------------------------------------------------------------------------
# BehaviorExtractor
# ---------------------------------------------------------------------------

def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def _count_hits(text: str, phrases: List[str]) -> int:
    """Word-boundary match — prevents 'send' matching 'sender', 'pay' matching 'payment'."""
    t = _norm(text)
    return sum(
        1 for p in phrases
        if re.search(r"\b" + re.escape(p) + r"\b", t)
    )


_FUTURE_RE = re.compile(
    r"\b(next time|tomorrow|weekend|soon|let's|we should|looking forward|can't wait|plan)\b",
    re.IGNORECASE,
)
_CALLBACK_RE = re.compile(
    r"\b(remember|you said|last time|earlier|as we discussed|as you mentioned)\b",
    re.IGNORECASE,
)


class BehaviorExtractor:
    """
    Extracts behavioral safety signals from a list of conversation turns.

    Expected turn format:
        {"turn_id": str, "sender": "user" | "other", "text": str}

    Returns BehaviorProfile. Never raises — returns zeroed profile on empty input.
    """

    def extract(self, turns: List[Dict]) -> BehaviorProfile:
        if not turns:
            logger.debug("BehaviorExtractor: empty input — returning zeroed profile")
            return BehaviorProfile()

        try:
            return self._extract(turns)
        except Exception as e:
            logger.error("BehaviorExtractor: extraction failed — %s", e, exc_info=True)
            return BehaviorProfile(degraded=True)

    def _extract(self, turns: List[Dict]) -> BehaviorProfile:
        user_turns  = [t for t in turns if t.get("sender") == "user"]
        other_turns = [t for t in turns if t.get("sender") == "other"]

        # Fallback: if sender field absent, alternate
        if not user_turns and not other_turns:
            user_turns  = turns[::2]
            other_turns = turns[1::2]

        total        = len(turns)
        user_count   = len(user_turns)
        other_count  = len(other_turns)
        all_other_text = " ".join(t.get("text", "") for t in other_turns)
        all_text       = " ".join(t.get("text", "") for t in turns)

        # --- Engagement scores ---
        reciprocity_score = self._reciprocity(user_count, other_count, total)
        initiative_score  = self._initiative(other_turns, total)
        engagement_depth  = self._engagement_depth(turns)
        continuity_score  = self._continuity(all_text)
        forward_movement  = self._forward_movement(all_text)
        asymmetry_score   = self._asymmetry(other_count, total)

        # --- Safety signals (from "other" turns only — that's the party being analysed) ---
        financial_mentions   = _count_hits(all_other_text, _FINANCIAL_TERMS)
        urgency_mentions     = _count_hits(all_other_text, _URGENCY_TERMS)
        isolation_mentions   = _count_hits(all_other_text, _ISOLATION_TERMS)
        pressure_phrase_hits = _count_hits(all_other_text, _PRESSURE_PHRASES)

        # Normalise to [0, 1] against reasonable maximums
        urgency_score   = _clamp(urgency_mentions / 4.0)
        isolation_score = _clamp(isolation_mentions / 3.0)

        # Pressure = weighted combination of financial, urgency, isolation
        financial_weight = _clamp(financial_mentions / 3.0) * 0.50
        urgency_weight   = urgency_score * 0.30
        isolation_weight = isolation_score * 0.20
        pressure_score   = _clamp(financial_weight + urgency_weight + isolation_weight)

        # Hard gate: pressure crosses threshold AND conversation is asymmetric
        deterministic_flag = (
            pressure_score >= PRESSURE_THRESHOLD
            and reciprocity_score <= ASYMMETRY_THRESHOLD
        )

        logger.debug(
            "BehaviorExtractor: turns=%d pressure=%.3f reciprocity=%.3f flag=%s",
            total, pressure_score, reciprocity_score, deterministic_flag,
        )

        return BehaviorProfile(
            reciprocity_score      = round(reciprocity_score, 4),
            initiative_score       = round(initiative_score, 4),
            engagement_depth_score = round(engagement_depth, 4),
            continuity_score       = round(continuity_score, 4),
            forward_movement_score = round(forward_movement, 4),
            pressure_score         = round(pressure_score, 4),
            isolation_score        = round(isolation_score, 4),
            urgency_score          = round(urgency_score, 4),
            asymmetry_score        = round(asymmetry_score, 4),
            financial_mentions     = financial_mentions,
            urgency_mentions       = urgency_mentions,
            isolation_mentions     = isolation_mentions,
            pressure_phrase_hits   = pressure_phrase_hits,
            deterministic_flag     = deterministic_flag,
            turn_count             = total,
            other_turn_count       = other_count,
            user_turn_count        = user_count,
            degraded               = False,
        )

    # -----------------------------------------------------------------------
    # Score helpers
    # -----------------------------------------------------------------------

    def _reciprocity(self, user_count: int, other_count: int, total: int) -> float:
        """How balanced is the conversation? 1.0 = perfectly even."""
        if total == 0:
            return 0.0
        ratio = min(user_count, other_count) / max(max(user_count, other_count), 1)
        return _clamp(ratio)

    def _initiative(self, other_turns: List[Dict], total: int) -> float:
        """
        How often does the 'other' party start new topics?
        Proxy: question count in other turns / total turns.
        """
        if not other_turns or total == 0:
            return 0.0
        q_count = sum(
            len(_QUESTION_RE.findall(t.get("text", "")))
            for t in other_turns
        )
        return _clamp(q_count / max(total, 1))

    def _engagement_depth(self, turns: List[Dict]) -> float:
        """
        Average message length normalised against a reasonable ceiling (50 words).
        """
        if not turns:
            return 0.0
        avg_words = sum(
            len(_WORD_RE.findall(t.get("text", ""))) for t in turns
        ) / len(turns)
        return _clamp(avg_words / 50.0)

    def _continuity(self, all_text: str) -> float:
        """Presence of callbacks to earlier conversation content."""
        hits = len(_CALLBACK_RE.findall(all_text))
        return _clamp(hits / 3.0)

    def _forward_movement(self, all_text: str) -> float:
        """Presence of future-oriented language."""
        hits = len(_FUTURE_RE.findall(all_text))
        return _clamp(hits / 5.0)

    def _asymmetry(self, other_count: int, total: int) -> float:
        """
        How dominant is 'other' in turn count? 1.0 = all turns from other.
        High asymmetry + high pressure = coercion risk.
        """
        if total == 0:
            return 0.0
        return _clamp(other_count / total)


# ---------------------------------------------------------------------------
# Public function called by api.py
# ---------------------------------------------------------------------------

_extractor = BehaviorExtractor()


def analyze_behavior(turns: List[Dict]) -> Dict[str, Any]:
    """
    Public entry point called by api.py via asyncio.to_thread.

    Accepts the same turn format as BehaviorExtractor.extract().
    Returns a dict matching the BehaviorResult schema fields.
    """
    profile = _extractor.extract(turns)
    return profile.to_schema_dict()