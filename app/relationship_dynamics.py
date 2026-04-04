"""
app/relationship_dynamics.py

Minimal relationship-dynamics engine for VibeLenz.
Rewritten to use package-safe imports and a stable interface.

Goal:
- avoid import-chain failures
- provide a predictable analyze_dynamics(turns) function
- return a RelationshipInsight model when possible
"""

from __future__ import annotations

from typing import List

from app.schemas import RelationshipInsight, Turn


def _safe_text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _turn_text(turn) -> str:
    if isinstance(turn, dict):
        return _safe_text(turn.get("text", ""))
    return _safe_text(getattr(turn, "text", ""))


def _turn_speaker(turn) -> str:
    if isinstance(turn, dict):
        return _safe_text(turn.get("speaker", "unknown")) or "unknown"
    return _safe_text(getattr(turn, "speaker", "unknown")) or "unknown"


def _infer_relationship_stage(turn_count: int) -> str:
    if turn_count <= 2:
        return "initial_contact"
    if turn_count <= 6:
        return "early_exchange"
    if turn_count <= 15:
        return "developing"
    return "established"


def _infer_tone(full_text: str) -> str:
    text = full_text.lower()

    positive_markers = ["haha", "lol", "lmao", "😊", "😂", "❤️", "cute", "miss you"]
    negative_markers = ["whatever", "fine", "k.", "leave me alone", "shut up", "annoying"]

    pos = sum(1 for x in positive_markers if x in text)
    neg = sum(1 for x in negative_markers if x in text)

    if neg > pos:
        return "tense"
    if pos > 0:
        return "warm"
    return "neutral"


def _infer_reciprocity(turns: List[Turn]) -> str:
    speakers = []
    for t in turns:
        speaker = _turn_speaker(t)
        if speaker not in speakers:
            speakers.append(speaker)

    if len(speakers) >= 2:
        return "mutual"
    return "one_sided"


def _infer_investment_level(full_text: str, turn_count: int) -> str:
    text = full_text.lower()

    if any(x in text for x in ["miss you", "care about you", "love you", "proud of you"]):
        return "high"
    if turn_count >= 6 or "?" in text:
        return "moderate"
    return "low"


def _infer_conflict_level(full_text: str) -> str:
    text = full_text.lower()

    if any(x in text for x in ["shut up", "leave me alone", "done with you", "never talk again"]):
        return "high"
    if any(x in text for x in ["annoying", "whatever", "fine", "k."]):
        return "moderate"
    return "low"


def analyze_dynamics(turns: List[Turn]) -> RelationshipInsight:
    """
    Analyze relationship dynamics from parsed turns.

    Returns a RelationshipInsight object from app.schemas.
    """
    turns = turns or []

    text_parts = [_turn_text(t) for t in turns if _turn_text(t)]
    full_text = " ".join(text_parts)
    turn_count = len(turns)

    stage = _infer_relationship_stage(turn_count)
    tone = _infer_tone(full_text)
    reciprocity = _infer_reciprocity(turns)
    investment = _infer_investment_level(full_text, turn_count)
    conflict = _infer_conflict_level(full_text)

    summary = (
        f"Stage: {stage}. Tone: {tone}. Reciprocity: {reciprocity}. "
        f"Investment: {investment}. Conflict: {conflict}."
    )

    # Build the model with the most likely fields first.
    # If schemas.py has extra defaults, Pydantic will handle them.
    return RelationshipInsight(
        relationship_stage=stage,
        overall_tone=tone,
        reciprocity_level=reciprocity,
        investment_level=investment,
        conflict_level=conflict,
        summary=summary,
    )