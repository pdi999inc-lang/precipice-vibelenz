from __future__ import annotations

# ---------------------------------------------------------------------------
# connection_humanizer.py — VibeLenz post-processing layer
# ---------------------------------------------------------------------------
# Translates low-risk analyzer output from safety language into human-readable
# connection framing. Must be called AFTER analyze_text() from analyzer_combined.
#
# FIXES APPLIED
# -------------
# [B1] pressure_present now included in the danger gate (was read but unused).
# [B2] "your cute" → "you're cute" (apostrophe fix; was silently never matching).
# [B3] Removed duplicate "yay" from flirt_markers (kept in warmth_markers only).
#
# [D1] Hardcoded gendered pronoun "She opens confused..." removed from reasoning
#      strings — all copy now uses gender-neutral language, consistent with the
#      VIE governance rule: no sex/gender inference from writing style.
# [D2] Generic fallback no longer unconditionally sets presentation_mode="connection"
#      for medium-risk results. A result with risk_score 25-69 and pressure_present
#      now stays in "risk" mode even without extraction.
#
# [T1] humanize_connection_result now reads pre-computed connection signals from
#      the analyzer result dict (connection_label, confusion_count, repair_count,
#      playful_count, warm_count, sexual_count) instead of re-scanning raw text.
#      Raw text scan is kept as a fallback when those fields are absent, so the
#      function remains safe to call on bare dicts or legacy payloads.
# ---------------------------------------------------------------------------

from typing import Any, Dict, List


DANGER_LANES = {"FRAUD", "COERCION_RISK"}


def _has_any(text: str, phrases: List[str]) -> bool:
    return any(p in text for p in phrases)


def _dedupe(items: List[str]) -> List[str]:
    seen: set = set()
    out: List[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _read_connection_signals(result: Dict[str, Any], text: str) -> Dict[str, bool]:
    """
    [T1] Prefer pre-computed connection signals from the analyzer result dict.
    Falls back to raw text scanning for legacy/bare dicts that lack these fields.
    """
    # Fast path: analyzer_combined already computed these
    if "confusion_count" in result or "connection_label" in result:
        confusion = int(result.get("confusion_count", 0) or 0) >= 1
        repair = int(result.get("repair_count", 0) or 0) >= 1
        recognition = repair  # repair implies recognition in the analyzer model
        flirt = int(result.get("playful_count", 0) or 0) >= 1 or int(result.get("sexual_count", 0) or 0) >= 1
        warmth = int(result.get("warm_count", 0) or 0) >= 1

        # connection_label is a stronger signal — use it to override if present
        label = str(result.get("connection_label") or "")
        if label == "playful_reengagement":
            confusion = repair = recognition = flirt = warmth = True
        elif label == "confusion_then_repair":
            confusion = repair = recognition = True
        elif label in {"light_sexual_reciprocity", "casual_flirtation"}:
            flirt = warmth = True
        elif label == "warm_receptivity":
            warmth = True

        return {
            "confusion": confusion,
            "apology": repair,
            "recognition": recognition,
            "flirt": flirt,
            "warmth": warmth,
        }

    # Fallback: raw text scan (legacy payloads)
    t = (text or "").lower()

    confusion_markers = [
        "who is this", "i don't know you", "idk who this is", "i dont know you",
    ]
    apology_markers = [
        "i'm so sorry", "im so sorry", "my apologies", "sorry",
        "it was just random", "i got a new phone", "all my stuff got deleted",
    ]
    recognition_markers = ["i remember you", "we talked", "where did we meet"]
    flirt_markers = [
        "your genes", "you wanted my baby", "i still do",
        "you're cute",  # [B2] fixed: was "your cute"
        "do you?",
    ]
    warmth_markers = [
        "that's so cool", "right!!! yes", "i remember you", "my apologies", "yay",
    ]

    return {
        "confusion": _has_any(t, confusion_markers),
        "apology": _has_any(t, apology_markers),
        "recognition": _has_any(t, recognition_markers),
        "flirt": _has_any(t, flirt_markers),
        "warmth": _has_any(t, warmth_markers),
    }


def humanize_connection_result(result: Dict[str, Any], extracted_text: str) -> Dict[str, Any]:
    lane = str(result.get("lane", "")).upper()
    risk_score = int(result.get("risk_score", 0) or 0)
    extraction_present = bool(result.get("extraction_present", False))
    pressure_present = bool(result.get("pressure_present", False))  # [B1] now used below

    # [B1] pressure_present added to danger gate
    # [D2] Medium-risk results (25-69) with pressure stay in risk mode even without extraction
    if (
        lane in DANGER_LANES
        or risk_score >= 70
        or extraction_present
        or pressure_present  # [B1]
    ):
        result["presentation_mode"] = "risk"
        return result

    # [D2] Additional soft gate: don't apply connection framing to elevated-but-not-dangerous scores
    # unless there are clear connection signals. Scores 25-69 without pressure get a chance to
    # qualify via strong connection pattern below, but fall through to risk mode if they don't.
    elevated = risk_score >= 25

    sigs = _read_connection_signals(result, extracted_text)
    confusion = sigs["confusion"]
    apology = sigs["apology"]
    recognition = sigs["recognition"]
    flirt = sigs["flirt"]
    warmth = sigs["warmth"]

    # Strongest pattern: confusion → apology/repair → recognition/flirt
    if confusion and apology and (recognition or flirt or warmth):
        result["presentation_mode"] = "connection"
        result["analysis_mode"] = "connection_read"
        result["primary_label"] = "playful_reengagement"

        result["summary"] = "This feels like playful reconnection after a brief moment of confusion."
        result["diagnosis"] = "This starts as a mix-up and turns into renewed warmth."
        # [D1] Removed hardcoded "She opens confused..." — gender-neutral rewrite
        result["reasoning"] = (
            "The conversation opens with confusion about who is texting, then quickly softens — "
            "an apology, recognition, and a shift into warmer, more playful engagement. "
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
        # [D1] Gender-neutral rewrite
        result["reasoning"] = (
            "The conversation starts off disoriented, but the apology and recognition shift "
            "the tone toward reconnection rather than distance."
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

    # [D2] Elevated risk with no strong connection pattern → stay in risk mode
    if elevated:
        result["presentation_mode"] = "risk"
        return result

    # Generic connection-side softening: risk_score < 25, no danger signals, no connection pattern
    result["presentation_mode"] = "connection"
    result["analysis_mode"] = "connection_read"

    if risk_score == 0:
        result["summary"] = "This reads more like a human interaction than a risk pattern."
        result["diagnosis"] = "Nothing here strongly points to danger."
        result["reasoning"] = (
            "The conversation may be incomplete or uneven, but it does not read like fraud, "
            "coercion, or extraction as the main story."
        )
        result["practical_next_steps"] = (
            "Focus on tone, consistency, and whether the energy becomes warmer, flatter, "
            "or more invested over time."
        )
        result["accountability"] = (
            "Do not force a dramatic interpretation when the stronger read is simply "
            "low-risk human behavior."
        )

    return result
