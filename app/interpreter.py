from __future__ import annotations

from typing import Any, Dict, List


def _join(items: List[str]) -> str:
    items = [str(x) for x in (items or []) if str(x).strip()]
    return ", ".join(items) if items else "none"


def interpret_analysis(analysis: Dict[str, Any]) -> Dict[str, Any]:
    lane = analysis.get("lane", "BENIGN")
    primary_label = analysis.get("primary_label", "routine_message")
    risk_level = analysis.get("risk_level", "LOW")
    reciprocity = analysis.get("reciprocity_level", "LOW")
    intent = analysis.get("intent_horizon", "UNCLEAR")
    key_signals = analysis.get("key_signals", [])
    key_dampeners = analysis.get("key_dampeners", [])
    alternatives = analysis.get("alternative_explanations", [])

    if lane == "FRAUD":
        diagnosis = "This looks more transactionally risky than socially normal."
        reasoning = (
            f"The strongest supported lane is FRAUD. Key signals: {_join(key_signals)}. "
            f"Dampeners present: {_join(key_dampeners)}."
        )
        practical_next_steps = "Do not send money or sensitive information. Slow the interaction down and verify ownership, identity, or platform legitimacy independently."
        accountability = "Do not talk yourself out of obvious extraction signals just because the tone looks polite or routine."
    elif lane == "COERCION_RISK":
        diagnosis = "This shows pressure that goes beyond normal ambiguity."
        reasoning = (
            f"The lane is COERCION_RISK because pressure and boundary-related signals are present. "
            f"Key signals: {_join(key_signals)}."
        )
        practical_next_steps = "Pause the interaction, restate the boundary once, and watch whether pressure persists."
        accountability = "Do not downgrade pressure just because there are moments of warmth or apparent responsiveness."
    elif lane == "DATING_AMBIGUOUS":
        diagnosis = "This looks more ambiguous than dangerous."
        reasoning = (
            f"The lane is DATING_AMBIGUOUS with primary label '{primary_label}'. "
            f"Reciprocity is {reciprocity}, intent horizon is {intent}, and there are no hard extraction/coercion criteria driving escalation."
        )
        practical_next_steps = "Slow the interaction down slightly and check for consistency over time instead of forcing a high-risk conclusion."
        accountability = "Do not convert fast or sexual mutual escalation into danger by default when coercion and extraction are absent."
    elif lane == "RELATIONSHIP_NORMAL":
        diagnosis = "This looks more like ordinary relationship communication than manipulation."
        reasoning = (
            f"The lane is RELATIONSHIP_NORMAL. Signals do not support fraud or coercion. "
            f"Key dampeners: {_join(key_dampeners)}."
        )
        practical_next_steps = "Focus on clarity, consistency, and whether concerns are resolved over time."
        accountability = "Do not escalate normal conflict or emotional tone into manipulation labels without harder evidence."
    else:
        diagnosis = "This looks low-risk and routine."
        reasoning = (
            f"The lane is BENIGN. Primary label: '{primary_label}'. "
            f"The conversation lacks hard danger criteria. Alternative explanations remain: {_join(alternatives)}."
        )
        practical_next_steps = "No urgent intervention is needed. Just keep normal verification habits appropriate to the context."
        accountability = "Do not manufacture risk where the evidence currently supports a routine explanation."

    return {
        "diagnosis": diagnosis,
        "reasoning": reasoning,
        "practical_next_steps": practical_next_steps,
        "accountability": accountability,
    }
