from __future__ import annotations
from typing import Any, Dict, List


DANGER_LANES = {"FRAUD", "COERCION_RISK"}


def _has_any(text: str, phrases: List[str]) -> bool:
    return any(p in text for p in phrases)


def _dedupe(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def humanize_connection_result(result: Dict[str, Any], extracted_text: str) -> Dict[str, Any]:
    text = (extracted_text or "").lower()

    lane = str(result.get("lane", "")).upper()
    risk_score = int(result.get("risk_score", 0) or 0)
    extraction_present = bool(result.get("extraction_present", False))
    pressure_present = bool(result.get("pressure_present", False))

    # If this is genuinely dangerous, do not force warm connection framing.
    if lane in DANGER_LANES or risk_score >= 70 or extraction_present:
        result["presentation_mode"] = "risk"
        return result

    confusion_markers = [
        "who is this",
        "i don't know you",
        "idk who this is",
        "i dont know you",
    ]
    apology_markers = [
        "i'm so sorry",
        "im so sorry",
        "my apologies",
        "sorry",
        "it was just random",
        "i got a new phone",
        "all my stuff got deleted",
    ]
    recognition_markers = [
        "i remember you",
        "we talked",
        "where did we meet",
    ]
    flirt_markers = [
        "your genes",
        "you wanted my baby",
        "i still do",
        "your cute",
        "yay",
        "do you?",
    ]
    warmth_markers = [
        "that's so cool",
        "right!!! yes",
        "i remember you",
        "my apologies",
        "yay",
    ]

    confusion = _has_any(text, confusion_markers)
    apology = _has_any(text, apology_markers)
    recognition = _has_any(text, recognition_markers)
    flirt = _has_any(text, flirt_markers)
    warmth = _has_any(text, warmth_markers)

    # Strongest pattern: confusion -> apology/repair -> recognition/flirt
    if confusion and apology and (recognition or flirt or warmth):
        result["presentation_mode"] = "connection"
        result["analysis_mode"] = "connection_read"
        result["primary_label"] = "playful_reengagement"

        result["summary"] = "This feels like playful reconnection after a brief moment of confusion."
        result["diagnosis"] = "This starts as a mix-up and turns into renewed warmth."
        result["reasoning"] = (
            "She opens confused about who is texting her, then quickly softens, apologizes, "
            "remembers you, and starts engaging in a warmer, more playful way. "
            "The overall movement is toward receptivity, not distance."
        )
        result["practical_next_steps"] = (
            "Keep it human and light. Stay with the playful energy and let the conversation "
            "be about the two of you instead of turning it into a product demo."
        )
        result["accountability"] = (
            "Do not flatten a warm moment into analytics. The strongest signal here is repair "
            "followed by renewed interest."
        )

        result["key_signals"] = _dedupe([
            "initial_confusion",
            "repair_attempt",
            "recognition_return" if recognition else "",
            "playful_flirtation" if flirt else "",
            "warming_tone" if warmth else "",
        ])

        result["key_dampeners"] = _dedupe([
            "no_extraction",
            "no_pressure",
            "mutual_warmth" if warmth else "",
            "social_repair_present",
        ])

        result["alternative_explanations"] = _dedupe([
            "brief contact confusion",
            "apology and repair",
            "playful reconnection",
        ])

        result["human_read"] = "playful_reengagement"
        return result

    # Softer fallback: apology + recognition without strong flirtation
    if apology and recognition:
        result["presentation_mode"] = "connection"
        result["analysis_mode"] = "connection_read"
        result["primary_label"] = "confusion_then_repair"

        result["summary"] = "This looks like confusion that gets repaired, not a negative turn."
        result["diagnosis"] = "There is some initial friction, but the tone recovers."
        result["reasoning"] = (
            "The conversation starts off disoriented, but the apology and recognition shift the tone "
            "toward reconnection rather than distance."
        )
        result["practical_next_steps"] = (
            "Keep the interaction grounded and personal. Let the recovery in tone do the work."
        )
        result["accountability"] = (
            "Do not over-interpret the awkward opening once the conversation clearly softens."
        )

        result["key_signals"] = _dedupe([
            "initial_confusion",
            "repair_attempt",
            "recognition_return",
        ])

        result["key_dampeners"] = _dedupe([
            "no_extraction",
            "no_pressure",
            "tone_recovery",
        ])

        result["alternative_explanations"] = _dedupe([
            "number confusion",
            "apology and repair",
            "ordinary reconnection",
        ])

        result["human_read"] = "confusion_then_repair"
        return result

    # Generic connection-side softening when risk is low and no danger criteria are present
    result["presentation_mode"] = "connection"
    result["analysis_mode"] = "connection_read"

    if result.get("risk_score", 0) == 0:
        result["summary"] = "This reads more like a human interaction than a risk pattern."
        result["diagnosis"] = "Nothing here strongly points to danger."
        result["reasoning"] = (
            "The conversation may be incomplete or uneven, but it does not read like fraud, coercion, "
            "or extraction as the main story."
        )
        result["practical_next_steps"] = (
            "Focus on tone, consistency, and whether the energy becomes warmer, flatter, or more invested over time."
        )
        result["accountability"] = (
            "Do not force a dramatic interpretation when the stronger read is simply low-risk human behavior."
        )

    return result
