"""
relationship_dynamics.py - VibeLenz Relationship Intelligence Engine

Pipeline position:
    OCR → turn parser → behavior.py (safety)
                      → relationship_dynamics.py (THIS) → RelationshipInsight
                      ↓ (both feed into)
                    AnalysisResponse

Design constraints:
    - Consumes verified turn format: {"turn_id": str, "sender": "user"|"other", "text": str}
    - Produces RelationshipInsight (schemas.py) — the primary consumer-facing output
    - No ML, no embeddings — deterministic signal extraction only
    - Fail-closed: any error returns None (caller omits relationship field from response)
    - Minimum 3 turns required — returns None below threshold
    - Does not duplicate behavior.py signal detection (pressure/fraud handled there)
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from schemas import RelationshipInsight

logger = logging.getLogger("vibelenz.relationship")

# ---------------------------------------------------------------------------
# Minimum turn threshold
# ---------------------------------------------------------------------------

MIN_TURNS = 3


# ---------------------------------------------------------------------------
# Signal patterns
# ---------------------------------------------------------------------------

_BUILDING_SIGNALS = re.compile(
    r"\b(let's|we should|next time|when we|can't wait|looking forward|excited|soon|tomorrow|weekend|plan)\b",
    re.IGNORECASE,
)

_FADING_SIGNALS = re.compile(
    r"\b(maybe|we'll see|sometime|busy|later|tired|whatever|fine|sure)\b",
    re.IGNORECASE,
)

_QUESTION_PATTERN = re.compile(r"\?")

_CALLBACK_PATTERN = re.compile(
    r"\b(remember|you said|last time|earlier|as we discussed|as we talked)\b",
    re.IGNORECASE,
)

_FUTURE_TOGETHER_PATTERN = re.compile(
    r"\b(we should|let's|next time|when we|our)\b",
    re.IGNORECASE,
)

_FUTURE_AVOIDING_PATTERN = re.compile(
    r"\b(we'll see|maybe|sometime|who knows)\b",
    re.IGNORECASE,
)

_RUSHING_PATTERN = re.compile(
    r"\b(soul ?mate|love you|marry|forever|destiny|meant to be|perfect for me)\b",
    re.IGNORECASE,
)

_HUMOR_PATTERN = re.compile(
    r"(haha|lol|😂|🤣|😄|funny|😊)",
    re.IGNORECASE,
)

_PLAYFUL_PATTERN = re.compile(
    r"(😉|😏|😘|tease|kidding|joking)",
    re.IGNORECASE,
)

_ENTHUSIASM_PATTERN = re.compile(
    r"\b(excited|can't wait|looking forward|amazing|love that|love this|so good)\b",
    re.IGNORECASE,
)

_SHARED_PATTERN = re.compile(
    r"\b(same|me too|exactly|totally|same here|i know right)\b",
    re.IGNORECASE,
)

_AVAILABILITY_CONCERN_PATTERN = re.compile(
    r"\b(busy|maybe|we'll see|sometime|later)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

AVG_LENGTH_HIGH = 15    # words — considered high engagement
AVG_LENGTH_LOW = 4      # words — considered low engagement


# ---------------------------------------------------------------------------
# Main analyzer
# ---------------------------------------------------------------------------

class RelationshipAnalyzer:
    """
    Extracts relationship dynamics from parsed conversation turns.

    Expected turn format (matches EvidenceLinker and behavior.py):
        {"turn_id": "T1", "sender": "user" | "other", "text": "..."}

    "user"  = the VibeLenz user
    "other" = the party being analyzed
    """

    def analyze(self, turn_history: List[Dict]) -> Optional[RelationshipInsight]:
        """
        Main entry point. Returns None on insufficient data or any error.
        Caller omits relationship field from AnalysisResponse when None returned.
        """
        if not turn_history or len(turn_history) < MIN_TURNS:
            logger.info(
                "RelationshipAnalyzer: insufficient turns (%d < %d)",
                len(turn_history) if turn_history else 0, MIN_TURNS,
            )
            return None

        try:
            return self._analyze(turn_history)
        except Exception as e:
            logger.error("RelationshipAnalyzer: analysis failed — %s", e)
            return None  # fail-closed

    def _analyze(self, turns: List[Dict]) -> RelationshipInsight:
        user_turns = [t for t in turns if t.get("sender") == "user"]
        other_turns = [t for t in turns if t.get("sender") == "other"]

        # Graceful fallback if sender field missing — alternate assignment
        if not user_turns and not other_turns:
            user_turns = turns[::2]
            other_turns = turns[1::2]

        all_other_text = " ".join(t.get("text", "") for t in other_turns)
        all_text = " ".join(t.get("text", "") for t in turns)
        total = len(turns)

        # --- Core dynamics ---
        energy_balance = self._energy_balance(user_turns, other_turns, total)
        momentum_direction = self._momentum(turns)
        intimacy_progression = self._intimacy(turns, other_turns, total)
        relationship_stage = self._stage(turns, intimacy_progression)

        # --- Scores ---
        momentum_score = self._score_momentum(turns, momentum_direction, all_text)
        compatibility_score = self._score_compatibility(turns, all_text)
        sustainability_score = self._score_sustainability(energy_balance, all_text)

        # --- Narrative lists ---
        growth_indicators = self._growth_indicators(turns, all_other_text, all_text)
        potential_blockers = self._potential_blockers(turns, all_text)
        connection_highlights = self._connection_highlights(turns, all_text)
        tension_points = self._tension_points(turns, all_text)

        # --- Narrative strings ---
        story_arc = self._story_arc(
            total, energy_balance, momentum_direction,
            momentum_score, compatibility_score
        )
        next_step = self._next_step(momentum_direction, relationship_stage, energy_balance)

        return RelationshipInsight(
            momentum_direction=momentum_direction,
            energy_balance=energy_balance,
            intimacy_progression=intimacy_progression,
            relationship_stage=relationship_stage,
            momentum_score=round(momentum_score, 3),
            compatibility_score=round(compatibility_score, 3),
            sustainability_score=round(sustainability_score, 3),
            story_arc=story_arc,
            next_natural_step=next_step,
            growth_indicators=growth_indicators,
            potential_blockers=potential_blockers,
            connection_highlights=connection_highlights,
            tension_points=tension_points,
        )

    # -----------------------------------------------------------------------
    # Core dynamics
    # -----------------------------------------------------------------------

    def _energy_balance(
        self,
        user_turns: List[Dict],
        other_turns: List[Dict],
        total: int,
    ) -> str:
        if not user_turns or not other_turns:
            return "unclear"

        user_avg = sum(len(t.get("text", "").split()) for t in user_turns) / len(user_turns)
        other_avg = sum(len(t.get("text", "").split()) for t in other_turns) / len(other_turns)

        user_q = sum(len(_QUESTION_PATTERN.findall(t.get("text", ""))) for t in user_turns)
        other_q = sum(len(_QUESTION_PATTERN.findall(t.get("text", ""))) for t in other_turns)

        length_ratio = user_avg / max(other_avg, 1)

        # When neither party asks questions, the question axis is neutral —
        # don't use it to classify, or silent conversations misclassify as other_leading.
        if user_q == 0 and other_q == 0:
            if 0.7 <= length_ratio <= 1.4:
                return "balanced"
            elif length_ratio > 1.4:
                return "user_leading"
            elif length_ratio < 0.7:
                return "other_leading"
            return "unclear"

        question_ratio = user_q / max(other_q, 1)

        if 0.7 <= length_ratio <= 1.4 and 0.5 <= question_ratio <= 2.0:
            return "balanced"
        elif length_ratio > 1.4 or question_ratio > 2.0:
            return "user_leading"
        elif length_ratio < 0.7 or question_ratio < 0.5:
            return "other_leading"
        else:
            return "mismatched"

    def _momentum(self, turns: List[Dict]) -> str:
        if len(turns) < 3:
            return "unclear"

        recent_text = " ".join(t.get("text", "") for t in turns[-3:])
        building = len(_BUILDING_SIGNALS.findall(recent_text))
        fading = len(_FADING_SIGNALS.findall(recent_text))

        if building > fading and building > 0:
            return "building"
        elif fading > building and fading > 0:
            return "fading"
        elif len([t for t in turns[-3:] if len(t.get("text", "").split()) > 10]) >= 2:
            return "maintaining"
        else:
            return "unclear"

    def _intimacy(self, turns: List[Dict], other_turns: List[Dict], total: int) -> str:
        all_text = " ".join(t.get("text", "") for t in turns)

        if _RUSHING_PATTERN.search(all_text) and total < 10:
            return "rushing"

        other_text = " ".join(t.get("text", "") for t in other_turns)
        personal_hits = len(re.findall(
            r"\b(feel|think|love|like|enjoy|excited|hope|wish)\b",
            other_text, re.IGNORECASE
        ))
        casual_hits = len(re.findall(
            r"\b(hi|hey|good|nice|ok|okay|cool)\b",
            other_text, re.IGNORECASE
        ))

        if personal_hits > 0 and total >= 4:
            return "healthy"
        elif casual_hits > 0 and personal_hits == 0 and total > 8:
            return "stalled"
        else:
            return "unclear"

    def _stage(self, turns: List[Dict], intimacy: str) -> str:
        total = len(turns)

        if total < 3:
            return "initial_contact"
        elif intimacy == "rushing":
            return "moving_too_fast"
        elif total < 8:
            return "building_rapport"
        elif intimacy == "healthy":
            return "exploring_compatibility"
        else:
            return "building_rapport"

    # -----------------------------------------------------------------------
    # Scores
    # -----------------------------------------------------------------------

    def _score_momentum(self, turns: List[Dict], direction: str, all_text: str) -> float:
        base = {"building": 0.75, "maintaining": 0.55, "fading": 0.25, "unclear": 0.45}
        score = base.get(direction, 0.45)

        if len(turns) > 10:
            score += 0.05
        if _BUILDING_SIGNALS.search(all_text):
            score += 0.05
        if _ENTHUSIASM_PATTERN.search(all_text):
            score += 0.05

        return min(1.0, score)

    def _score_compatibility(self, turns: List[Dict], all_text: str) -> float:
        score = 0.4  # base

        # Natural flow: shared words between adjacent turns
        natural_responses = 0
        for i in range(1, len(turns)):
            prev_words = set(turns[i - 1].get("text", "").lower().split())
            curr_words = set(turns[i].get("text", "").lower().split())
            if len(prev_words & curr_words) > 0:
                natural_responses += 1

        flow_ratio = natural_responses / max(len(turns) - 1, 1)
        score += flow_ratio * 0.3

        # Humor and shared perspective
        if _HUMOR_PATTERN.search(all_text):
            score += 0.1
        if _PLAYFUL_PATTERN.search(all_text):
            score += 0.05
        if _SHARED_PATTERN.search(all_text):
            score += 0.1

        return min(1.0, score)

    def _score_sustainability(self, energy_balance: str, all_text: str) -> float:
        base = {
            "balanced": 0.8,
            "other_leading": 0.65,
            "user_leading": 0.55,
            "mismatched": 0.3,
            "unclear": 0.45,
        }
        score = base.get(energy_balance, 0.45)

        if _FUTURE_TOGETHER_PATTERN.search(all_text):
            score += 0.1
        if _FUTURE_AVOIDING_PATTERN.search(all_text):
            score -= 0.1

        return min(1.0, max(0.0, score))

    # -----------------------------------------------------------------------
    # Narrative lists
    # -----------------------------------------------------------------------

    def _growth_indicators(
        self, turns: List[Dict], other_text: str, all_text: str
    ) -> List[str]:
        indicators = []

        if _FUTURE_TOGETHER_PATTERN.search(all_text):
            indicators.append("Initiative toward future plans")
        if _ENTHUSIASM_PATTERN.search(other_text):
            indicators.append("Genuine enthusiasm expressed")
        if len([t for t in turns if _QUESTION_PATTERN.search(t.get("text", ""))]) >= 2:
            indicators.append("Sustained curiosity about each other")
        if _SHARED_PATTERN.search(all_text):
            indicators.append("Shared perspectives and common ground")
        if _CALLBACK_PATTERN.search(all_text):
            indicators.append("References to earlier conversation — active listening")

        return list(dict.fromkeys(indicators))

    def _potential_blockers(self, turns: List[Dict], all_text: str) -> List[str]:
        blockers = []

        if _AVAILABILITY_CONCERN_PATTERN.search(all_text):
            blockers.append("Availability or commitment uncertainty")
        if _FUTURE_AVOIDING_PATTERN.search(all_text):
            blockers.append("Avoidance of future plans")

        recent = turns[-3:] if len(turns) >= 3 else turns
        short_recent = [t for t in recent if len(t.get("text", "").split()) < 5]
        if len(short_recent) >= 2:
            blockers.append("Declining conversation energy in recent exchanges")

        if _RUSHING_PATTERN.search(all_text):
            blockers.append("Emotional intensity ahead of relationship foundation")

        return list(dict.fromkeys(blockers))

    def _connection_highlights(self, turns: List[Dict], all_text: str) -> List[str]:
        highlights = []

        if _SHARED_PATTERN.search(all_text):
            highlights.append("Shared perspectives or experiences")
        if _HUMOR_PATTERN.search(all_text):
            highlights.append("Natural humor and playfulness")
        if _ENTHUSIASM_PATTERN.search(all_text):
            highlights.append("Genuine enthusiasm about the interaction")
        if _CALLBACK_PATTERN.search(all_text):
            highlights.append("Callbacks to earlier moments — good memory and attention")

        return list(dict.fromkeys(highlights))

    def _tension_points(self, turns: List[Dict], all_text: str) -> List[str]:
        tensions = []

        if re.search(r"\b(confused|weird|awkward|unclear|not sure what you mean)\b", all_text, re.IGNORECASE):
            tensions.append("Uncertainty about intentions or meaning")
        if _AVAILABILITY_CONCERN_PATTERN.search(all_text):
            tensions.append("Potential availability concerns")

        recent = turns[-3:] if len(turns) >= 3 else turns
        if len([t for t in recent if len(t.get("text", "").split()) < 3]) >= 2:
            tensions.append("Minimal responses in recent exchanges")

        return list(dict.fromkeys(tensions))

    # -----------------------------------------------------------------------
    # Narrative strings
    # -----------------------------------------------------------------------

    def _story_arc(
        self,
        total: int,
        energy_balance: str,
        momentum: str,
        momentum_score: float,
        compatibility_score: float,
    ) -> str:
        if momentum == "building" and energy_balance == "balanced":
            return (
                f"Two people genuinely connecting over {total} exchanges — "
                "balanced effort, building energy, moving toward something real."
            )
        elif momentum == "building" and energy_balance == "other_leading":
            return "They're driving this forward and you're responding well — good energy coming from their side."
        elif momentum == "building" and energy_balance == "user_leading":
            return "You're taking more initiative and they're responding positively — momentum is real but one-sided for now."
        elif momentum == "maintaining" and compatibility_score > 0.6:
            return "Settling into a comfortable rhythm — genuine compatibility showing, steady without rushing."
        elif momentum == "fading":
            return "The energy is dropping off. Something shifted and the conversation is losing steam."
        elif momentum == "maintaining":
            return "Holding steady — engaged but not escalating yet."
        else:
            return f"Early stage with {total} exchanges — still finding the dynamic."

    def _next_step(
        self, momentum: str, stage: str, energy_balance: str
    ) -> str:
        if momentum == "building" and stage in ("building_rapport", "exploring_compatibility"):
            return "Keep building on this momentum — suggest something specific or move toward meeting if you haven't."
        elif momentum == "building" and energy_balance == "user_leading":
            return "Let them take some initiative — ask a question and see what direction they take it."
        elif momentum == "fading":
            return "Either inject fresh energy with a new topic or let this one wind down naturally."
        elif stage == "moving_too_fast":
            return "Slow the emotional pace — focus on getting to know them as a person before going deeper."
        elif momentum == "maintaining":
            return "Keep the steady rhythm going — look for a natural moment to deepen the connection."
        else:
            return "Give it a few more exchanges to see which direction this wants to go."


# ---------------------------------------------------------------------------
# Public function called by api.py
# ---------------------------------------------------------------------------

_analyzer = RelationshipAnalyzer()


def analyze_dynamics(turns: List[Dict]) -> Dict[str, Any]:
    """
    Public entry point called by api.py via asyncio.to_thread.

    Accepts the same turn format as RelationshipAnalyzer.analyze().
    Returns a dict (DynamicsResult field names) or a minimal dict with
    insufficient_data=True if below MIN_TURNS threshold.
    """
    insight = _analyzer.analyze(turns)

    if insight is None:
        return {
            "momentum_direction": "unclear",
            "energy_balance": "unclear",
            "intimacy_progression": "unclear",
            "relationship_stage": "initial_contact",
            "momentum_score": 0.0,
            "compatibility_score": 0.0,
            "sustainability_score": 0.0,
            "story_arc": "",
            "next_natural_step": "",
            "growth_indicators": [],
            "potential_blockers": [],
            "connection_highlights": [],
            "tension_points": [],
            "insufficient_data": True,
        }

    return insight.model_dump()
