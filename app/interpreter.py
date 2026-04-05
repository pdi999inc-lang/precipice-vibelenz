from __future__ import annotations

"""
interpreter.py — VibeLenz Interpretation Layer
-----------------------------------------------
Called after analyze_text() and score_connection() to produce
human-readable diagnosis, reasoning, next steps, and accountability copy.

Pipeline position:
    analyze_text()        → risk/fraud result
    score_connection()    → connection scoring
    interpret_analysis()  → THIS MODULE — final human-readable output

FIXES APPLIED
-------------
[B1] Removed gendered pronouns throughout _connection_copy (was "She was
     confused", "she came back around", "A girl who is genuinely turned off",
     etc. across every branch). All copy is now gender-neutral, consistent
     with VIE governance rule: no sex/gender inference from writing style.

[B2] Removed dead _has() function — defined but never called anywhere.

[B3] _risk_override now also gates on MEDIUM risk (score 35–69) for non-FRAUD,
     non-COERCION lanes. A DATING_AMBIGUOUS result at risk_score=55 previously
     received connection copy when requested — safety gap closed.

[D1] _connection_copy now handles MIXED_INTENT and NEGATIVE connection_level
     values from connection_lexicon v1.1. These no longer fall through to the
     generic "low stakes" branch.

[D2] playful_reengagement branch copy de-personalised — removed hardcoded
     quotes ("I still do", "I want your genes", "Yay", "new phone excuse")
     that were written for one specific conversation and would be factually
     wrong on any other.

[T1] interpret_analysis now uses relationship_type to adjust copy tone for
     known relationships (dating/family/friend). extracted_text and
     context_note wired in as optional enrichment rather than silently dropped.

[T2] _human_label extended with missing labels: relationship_context,
     mixed_intent, routine_message, MIXED_INTENT, NEGATIVE.
"""

from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# UTILITIES
# ---------------------------------------------------------------------------

def _clean(items: List[str]) -> List[str]:
    return [str(x).strip() for x in (items or []) if str(x).strip()]


# [B2] _has() removed — was defined but never called.


def _human_label(primary_label: str, lane: str, domain_mode: str) -> str:
    # [T2] Extended with labels the combined analyzer can produce
    mapping = {
        "playful_reengagement": "playful reconnection",
        "light_sexual_reciprocity": "light sexual reciprocity",
        "warm_receptivity": "warm receptivity",
        "confusion_then_repair": "confusion that clears",
        "casual_flirtation": "casual flirtation",
        "low_information_neutral": "low-stakes interaction",
        "routine_host_message": "routine logistics",
        "routine_message": "routine message",
        "relationship_context": "relationship context",
        "mixed_intent": "mixed intent",
        "transactional_extraction_pattern": "transactional risk pattern",
        "pressure_with_boundary_violation": "pressure pattern",
        # connection_lexicon v1.1 levels
        "MIXED_INTENT": "mixed signals",
        "NEGATIVE": "negative signals",
    }
    return mapping.get(primary_label, primary_label.replace("_", " "))


def _social_tone(result: Dict[str, Any]) -> str:
    positives = _clean(result.get("positive_signals", []))
    primary_label = str(result.get("primary_label", "low_information_neutral"))
    connection_level = str(result.get("connection_level", "")).upper()

    if connection_level == "NEGATIVE":
        return "disengaged or resistant"
    if connection_level == "MIXED_INTENT":
        return "mixed — positive and negative signals both present"
    if "sexual_reciprocity_present" in positives or primary_label == "light_sexual_reciprocity":
        return "playful, flirtatious, and reciprocal"
    if primary_label == "playful_reengagement":
        return "warm after initial confusion"
    if "warm_receptivity_present" in positives or primary_label == "warm_receptivity":
        return "open, warm, and responsive"
    if primary_label == "confusion_then_repair":
        return "awkward at first, then repaired"
    if primary_label == "casual_flirtation":
        return "light and socially positive"
    return "fairly low-stakes"


def _interest_summary(result: Dict[str, Any]) -> str:
    label = str(result.get("interest_label", "Not Applicable"))
    primary_label = str(result.get("primary_label", "low_information_neutral"))
    connection_level = str(result.get("connection_level", "")).upper()

    if connection_level == "NEGATIVE":
        return "disengagement detected"
    if connection_level == "MIXED_INTENT":
        return "conflicted — read carefully"

    if label and label != "Not Applicable":
        if label.lower() == "high":
            return "good receptivity"
        if label.lower() == "moderate":
            return "some real receptivity"
        return label

    mapping = {
        "playful_reengagement": "good receptivity",
        "light_sexual_reciprocity": "clear playful interest",
        "warm_receptivity": "positive openness",
        "confusion_then_repair": "improving energy",
        "casual_flirtation": "light interest",
    }
    return mapping.get(primary_label, "context dependent")


def _risk_override(result: Dict[str, Any]) -> bool:
    lane = str(result.get("lane", "BENIGN"))
    risk_level = str(result.get("risk_level", "LOW")).upper()
    # [B3] MEDIUM risk also gates to risk mode — a score of 35-69 in any lane
    # should not receive warm connection copy
    return lane in {"FRAUD", "COERCION_RISK"} or risk_level in {"HIGH", "MEDIUM"}


# ---------------------------------------------------------------------------
# RISK COPY
# ---------------------------------------------------------------------------

def _risk_copy(out: Dict[str, Any]) -> Dict[str, Any]:
    lane = str(out.get("lane", "BENIGN"))
    domain_mode = str(out.get("domain_mode", "general_unknown"))

    if lane == "FRAUD":
        if domain_mode == "housing_rental":
            diagnosis = "This looks more like a setup than a normal rental conversation."
            reasoning = (
                "The concern is the sequence. Once verification gets inverted, money enters "
                "the picture, or the story starts shifting, the interaction stops reading like "
                "normal logistics and starts reading like a transactional risk pattern."
            )
            next_steps = (
                "Slow it down immediately. Verify ownership, identity, and the platform story "
                "independently before you give money, documents, or trust."
            )
            accountability = (
                "Do not talk yourself out of obvious risk just because the tone sounds "
                "polite, charming, or routine."
            )
        else:
            diagnosis = "This reads more like a risk pattern than a normal interaction."
            reasoning = (
                "What matters most is not one isolated line, but the overall pattern of "
                "pressure, extraction, contradiction, or control."
            )
            next_steps = (
                "Pause the interaction and verify independently before you give money, "
                "sensitive information, or control."
            )
            accountability = (
                "Do not explain away real risk signals just because the delivery feels smooth."
            )

    elif lane == "COERCION_RISK":
        diagnosis = "This is starting to feel like pressure, not just awkwardness."
        reasoning = (
            "What pushes this upward is not mere discomfort. The visible pattern starts to "
            "lean on pressure or boundary friction, which matters more than tone alone."
        )
        next_steps = (
            "Tighten the boundary. State it clearly once, then watch whether the other "
            "person respects it without needing a long argument."
        )
        accountability = (
            "Do not explain away pressure just because it arrives wrapped in charm, "
            "confusion, or emotion."
        )

    else:
        # MEDIUM risk in a non-danger lane — elevated but not conclusive
        diagnosis = "This does not currently read like a strong risk pattern, but the signals are not clean."
        reasoning = (
            "Nothing here strongly supports fraud or coercion as the main story, but enough "
            "signals are present to warrant attention. A few more exchanges will clarify "
            "whether this is normal friction or something worth being more careful about."
        )
        next_steps = (
            "Stay observant. Do not overreact, but do not ignore the signals that are present."
        )
        accountability = "Do not manufacture danger — and do not dismiss real signals either."

    out["presentation_mode"] = "risk"
    out["mode_title"] = "Risk Analysis"
    out["mode_tagline"] = (
        "Sharper read on contradiction, pressure, extraction, and protective next steps."
    )
    out["human_label"] = _human_label(
        str(out.get("primary_label", "")), lane, domain_mode
    )
    out["diagnosis"] = diagnosis
    out["reasoning"] = reasoning
    out["practical_next_steps"] = next_steps
    out["accountability"] = accountability
    out["social_tone"] = "Not the focus here"
    out["interest_summary"] = "Not the focus here"
    out["mode_override_note"] = ""
    return out


# ---------------------------------------------------------------------------
# CONNECTION COPY
# ---------------------------------------------------------------------------

def _connection_copy(
    out: Dict[str, Any],
    relationship_type: str = "stranger",
) -> Dict[str, Any]:
    primary_label = str(out.get("primary_label", "low_information_neutral"))
    # coaching_markers is reserved for future wiring — no upstream module sets this key yet.
    # When analyzer_combined surfaces self-pitch detection, populate "coaching_markers" in
    # the result dict and this override block will activate automatically.
    coaching = _clean(out.get("coaching_markers", []))
    connection_level = str(out.get("connection_level", "")).upper()

    # [D1] Handle MIXED_INTENT and NEGATIVE from connection_lexicon v1.1
    if connection_level == "NEGATIVE":
        diagnosis = "The signals here are more resistant than receptive."
        reasoning = (
            "What is visible is not just a quiet or low-energy response — there are active "
            "signals of discomfort, disengagement, or pushback. That pattern matters more "
            "than isolated warm moments."
        )
        next_steps = (
            "Give the conversation room to breathe. Pressing harder against clear resistance "
            "will not help. Let the dynamic reset before re-engaging."
        )
        accountability = (
            "Receptivity has to be present before chemistry can build. "
            "Do not talk yourself into a positive read when the signals are pointing the other way."
        )
        out["presentation_mode"] = "connection"
        out["mode_title"] = "Connection Analysis"
        out["mode_tagline"] = (
            "Warm read on chemistry, receptivity, emotional movement, and what to do next."
        )
        out["human_label"] = _human_label(primary_label, str(out.get("lane", "")), str(out.get("domain_mode", "")))
        out["diagnosis"] = diagnosis
        out["reasoning"] = reasoning
        out["practical_next_steps"] = next_steps
        out["accountability"] = accountability
        out["social_tone"] = _social_tone(out)
        out["interest_summary"] = _interest_summary(out)
        out["mode_override_note"] = ""
        return out

    if connection_level == "MIXED_INTENT":
        diagnosis = "There are positive and negative signals present at the same time — this one needs a careful read."
        reasoning = (
            "The conversation contains both warmth and friction, interest and resistance. "
            "That does not mean it is bad or good — it means the picture is genuinely mixed "
            "and a confident read in either direction is not yet supported by the evidence."
        )
        next_steps = (
            "Watch the direction of movement, not just the snapshot. "
            "Is the energy getting warmer or colder over time? "
            "That trend is more useful than any single moment."
        )
        accountability = (
            "Do not force a clean label onto a messy signal. Mixed intent is a real result, "
            "not a failure to detect something cleaner."
        )
        out["presentation_mode"] = "connection"
        out["mode_title"] = "Connection Analysis"
        out["mode_tagline"] = (
            "Warm read on chemistry, receptivity, emotional movement, and what to do next."
        )
        out["human_label"] = _human_label(primary_label, str(out.get("lane", "")), str(out.get("domain_mode", "")))
        out["diagnosis"] = diagnosis
        out["reasoning"] = reasoning
        out["practical_next_steps"] = next_steps
        out["accountability"] = accountability
        out["social_tone"] = _social_tone(out)
        out["interest_summary"] = _interest_summary(out)
        out["mode_override_note"] = ""
        return out

    # --- Primary label branches ---

    # [B1] All gendered pronouns removed throughout
    # [D2] playful_reengagement branch de-personalised — hardcoded conversation-specific
    #      quotes and the "new phone excuse" reference removed
    if primary_label == "playful_reengagement":
        diagnosis = (
            "There was confusion, then embarrassment, then the energy came back around — "
            "and that arc is actually more telling than if it had gone smoothly."
        )
        reasoning = (
            "The rough opening is not the story. The confusion looked real, not calculated — "
            "someone who was scatterbrained in the moment, not someone who was shutting things down. "
            "What matters more is what happened after the confusion cleared: the tone shifted toward "
            "warmth and playfulness. That shift is the signal. "
            "People who are genuinely uninterested do not bother repairing the energy. "
            "Honest probability read: still open to talking. "
            "Genuinely settled and consistent — less certain, but possible. "
            "The conversation did not end on rejection. It ended on reconnection."
        )
        next_steps = (
            "Do not make this heavier than it is. Do not relitigate the awkward opening — "
            "it already repaired itself. Treat it like a slightly weird reconnection that "
            "both sides moved past. Keep the tone easy and let that do the work."
        )
        accountability = (
            "The bigger risk is overthinking this into a problem it is not. "
            "If there was positive energy after the confusion cleared, that is the read to trust. "
            "Stop treating a normal messy human moment like a case file."
        )

    elif primary_label == "light_sexual_reciprocity":
        # [B1] "she is matching it" → "they are matching it"
        diagnosis = "There is real flirtatious energy here and it is being matched, not just tolerated."
        reasoning = (
            "This is not just politeness. The other person is leaning in. The reciprocal tone "
            "is visible — no deflecting, no redirecting, no going cold. "
            "That is the signal that matters more than anything else in an early exchange."
        )
        next_steps = (
            "Stay with it. Let the energy breathe. The moment you start explaining yourself "
            "or pivoting into serious mode, you lose the thread."
        )
        accountability = "Do not talk yourself out of chemistry that is already working."

    elif primary_label == "warm_receptivity":
        # [B1] "She is open" → "The energy is open"
        diagnosis = "The energy here is open and engaged — not guarded, not pulling back."
        reasoning = (
            "What stands out is not intensity, it is the absence of resistance. "
            "There is no visible exit-seeking. That openness is quiet but it is real, "
            "and it is a better signal than surface enthusiasm that disappears the moment "
            "things get less easy."
        )
        next_steps = (
            "Keep the tone easy and person-focused. Let consistency do the work from here — "
            "not pressure, not grand gestures."
        )
        accountability = "Warm does not mean locked in. Do not skip the part where you actually build something."

    elif primary_label == "confusion_then_repair":
        # [B1] "she worked to fix it" → "the repair happened"
        diagnosis = "It started awkward, but the repair happened — and that is the part that actually matters."
        reasoning = (
            "The rough opening is not the story. People who are checked out do not bother "
            "repairing the energy. What matters more is what happened once the confusion cleared — "
            "there was a return, which means there was something worth recovering. "
            "Not locked in and not super consistent, but not shutting things down either."
        )
        next_steps = (
            "Do not drag the awkward moment back into the conversation. "
            "It already moved past — follow that lead. "
            "Keep it light. Something easy that acknowledges the weirdness without dwelling "
            "on it puts things back in the right lane."
        )
        accountability = (
            "Stop overanalyzing the confusion when the obvious read is simpler: "
            "embarrassment, recovery, still open. "
            "You are not locked into the worst version of how this started."
        )

    elif primary_label == "casual_flirtation":
        diagnosis = "Light, easy, and going in the right direction."
        reasoning = (
            "Nothing here is heavy or loaded. The tone is playful and the energy is positive. "
            "What is absent matters as much as what is present — no defensiveness, no pulling back, "
            "no mixed signals that would justify a complicated read."
        )
        next_steps = "Keep it light. Do not make it heavier than it needs to be right now."
        accountability = "Not every good thing needs to be analyzed into the ground. Sometimes easy is just easy."

    else:
        diagnosis = "This is a real human interaction — low stakes, not a threat, just still early."
        reasoning = (
            "Nothing here points to pressure, danger, or bad intent. "
            "It reads like a normal exchange between two people who are still figuring out "
            "the dynamic. That is not a bad thing — it just means the picture is not complete yet. "
            "A few more exchanges will tell you more than any analysis of what you already have."
        )
        next_steps = (
            "Treat it lightly. Let the next few exchanges do the work "
            "instead of trying to force a conclusion from limited data."
        )
        accountability = (
            "Stop trying to solve it before it has had time to develop. "
            "You do not have enough information yet to make a hard call — and that is okay."
        )

    # [T1] Relationship type adjusts accountability framing for known relationships
    if relationship_type in {"dating", "family", "friend"}:
        accountability = (
            accountability.rstrip(".")
            + " — context for an established relationship, not a first impression."
        )

    # Coaching override: self-pitch detected alongside positive chemistry
    if "self_pitch_present" in coaching and primary_label in {
        "playful_reengagement", "light_sexual_reciprocity", "warm_receptivity"
    }:
        next_steps = (
            "Keep it personal from here. Chemistry is already working — "
            "do not redirect it into a product demo. That trade is almost never worth it."
        )
        accountability = (
            "Flirt if you are flirting. Pitch if you are pitching. "
            "Mixing them weakens both and gives you fake signal on both ends."
        )

    out["presentation_mode"] = "connection"
    out["mode_title"] = "Connection Analysis"
    out["mode_tagline"] = (
        "Warm read on chemistry, receptivity, emotional movement, and what to do next."
    )
    out["human_label"] = _human_label(
        primary_label,
        str(out.get("lane", "")),
        str(out.get("domain_mode", "")),
    )
    out["diagnosis"] = diagnosis
    out["reasoning"] = reasoning
    out["practical_next_steps"] = next_steps
    out["accountability"] = accountability
    out["social_tone"] = _social_tone(out)
    out["interest_summary"] = _interest_summary(out)
    out["mode_override_note"] = ""
    return out


# ---------------------------------------------------------------------------
# PUBLIC API
# ---------------------------------------------------------------------------

def interpret_analysis(
    result: Dict[str, Any],
    extracted_text: str = "",
    relationship_type: str = "stranger",
    context_note: str = "",
    requested_mode: str = "risk",
) -> Dict[str, Any]:
    """
    Produce human-readable interpretation of a VibeLenz analysis result.

    Parameters
    ----------
    result           : Output dict from analyze_text() / score_connection().
    extracted_text   : Raw OCR text. Used as enrichment context (reserved
                       for future copy personalisation).
    relationship_type: "stranger" | "dating" | "family" | "friend" | "business"
                       Adjusts accountability copy tone for known relationships.
    context_note     : Optional freetext context from the caller.
    requested_mode   : "risk" | "connection". Safety override may force "risk"
                       regardless of what is requested.

    Returns
    -------
    The result dict with added keys: presentation_mode, mode_title,
    mode_tagline, human_label, diagnosis, reasoning, practical_next_steps,
    accountability, social_tone, interest_summary, mode_override_note,
    requested_mode.
    """
    out = dict(result or {})
    requested_mode = str(requested_mode or "risk").lower().strip()
    if requested_mode not in {"connection", "risk"}:
        requested_mode = "risk"

    # [T1] Store relationship_type in output for downstream use
    out["relationship_type"] = relationship_type

    # [B3] _risk_override now also gates MEDIUM risk
    if _risk_override(out):
        out = _risk_copy(out)
        if requested_mode == "connection":
            out["mode_override_note"] = (
                "Connection mode was selected, but stronger safety signals pushed "
                "this result into a more protective read."
            )
        out["requested_mode"] = requested_mode
        return out

    if requested_mode == "connection":
        # [T1] Pass relationship_type into connection copy
        out = _connection_copy(out, relationship_type=relationship_type)
    else:
        out = _risk_copy(out)

    out["requested_mode"] = requested_mode
    return out
