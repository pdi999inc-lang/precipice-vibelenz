from __future__ import annotations

import re
from typing import Any, Dict, List


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def _contains_any(text: str, phrases: List[str]) -> bool:
    t = _norm(text)
    return any(p in t for p in phrases)


def _count_any(text: str, phrases: List[str]) -> int:
    t = _norm(text)
    return sum(1 for p in phrases if p in t)


def _detect_domain_mode(text: str) -> Dict[str, Any]:
    t = _norm(text)

    housing_terms = [
        "rent", "rental", "deposit", "lease", "application", "landlord",
        "owner", "property", "apartment", "house", "utilities", "move in",
        "showing", "viewing", "zillow", "nextdoor", "airbnb", "host",
        "guest", "check in", "wifi", "parking"
    ]
    dating_terms = [
        "date", "dating", "cute", "beautiful", "babe", "baby", "kiss",
        "miss you", "love you", "come over", "hook up", "hookup", "sexy"
    ]
    marketplace_terms = [
        "facebook marketplace", "seller", "buyer", "pickup", "shipping",
        "tracking", "venmo", "cashapp", "paypal", "zelle"
    ]

    housing_score = _count_any(t, housing_terms)
    dating_score = _count_any(t, dating_terms)
    marketplace_score = _count_any(t, marketplace_terms)

    scores = {
        "housing_rental": housing_score,
        "dating_social": dating_score,
        "marketplace_transaction": marketplace_score,
    }

    best_mode = max(scores, key=scores.get)
    best_score = scores[best_mode]
    total = max(sum(scores.values()), 1)

    if best_score < 3:
        return {"domain_mode": "general_unknown", "domain_confidence": 0.35}

    return {
        "domain_mode": best_mode,
        "domain_confidence": round(best_score / total, 2),
    }


def _detect_reciprocity(text: str) -> str:
    t = _norm(text)
    medium = ["thanks", "thank you", "let me know", "sounds good", "okay", "ok"]
    high = ["me too", "you too", "haha", "lol", "we can", "let's"]

    if _count_any(t, high) >= 2:
        return "HIGH"
    if _count_any(t, high) >= 1 or _count_any(t, medium) >= 2:
        return "MEDIUM"
    return "LOW"


def _detect_intent_horizon(text: str, domain_mode: str) -> str:
    if domain_mode != "dating_social":
        return "UNCLEAR"

    t = _norm(text)
    short_term = ["come over", "hook up", "hookup", "sexy", "horny", "kiss"]
    long_term = ["relationship", "future", "tomorrow", "weekend", "plan", "consistency"]

    s = _count_any(t, short_term)
    l = _count_any(t, long_term)

    if s > l and s >= 1:
        return "SHORT_TERM"
    if l > s and l >= 1:
        return "LONG_TERM"
    return "UNCLEAR"


def _detect_connection_signals(text: str) -> Dict[str, Any]:
    t = _norm(text)

    confusion_markers = [
        "who is this", "who are you", "i don't know you", "i do not know you",
        "wrong number", "new phone", "got a new phone", "all my stuff got deleted",
        "all my contacts", "lost my contacts", "don't have your number",
        "do not have your number", "i forgot", "i don't remember", "i do not remember",
        "| don't know you", "| do not know you", "| forgot", "| don't remember",
    ]

    repair_markers = [
        "i remember", "i remember you", "oh i remember", "oh right",
        "my bad", "i'm sorry", "i am sorry", "so sorry", "my apologies",
        "it was random", "i apologize", "oh okay", "oh ok", "never mind",
        "wait i know", "that makes sense now",
        "| remember", "| remember you", "oh | remember",
    ]

    playful_markers = [
        "i still do", "i want your", "your genes", "yay", "haha", "lol",
        "you're cute", "you are cute", "miss you", "liked your", "can have them",
        "let me see", "send me", "you're funny", "you are funny",
        "| still do", "| want your", "| remember you",
    ]

    warm_markers = [
        "that's so cool", "that is so cool", "that's awesome", "that is awesome",
        "good app", "great app", "amazing", "wow", "impressed", "love that",
        "so cool", "really cool", "that's great", "that is great",
        "good fucking app", "fucking app",
    ]

    sexual_reciprocity_markers = [
        "i want your baby", "i want your genes", "i still do", "you're hot",
        "you are hot", "you're sexy", "you are sexy", "come over", "hook up",
        "| want your baby", "| want your genes", "| still do",
        "| want your", "wanted my baby", "want your genes",
    ]

    confusion_count = _count_any(t, confusion_markers)
    repair_count = _count_any(t, repair_markers)
    playful_count = _count_any(t, playful_markers)
    warm_count = _count_any(t, warm_markers)
    sexual_count = _count_any(t, sexual_reciprocity_markers)

    signals = []
    if warm_count >= 1:
        signals.append("warm_reception_present")
    if playful_count >= 1:
        signals.append("playful_engagement_present")
    if sexual_count >= 1:
        signals.append("sexual_reciprocity_present")
    if repair_count >= 1:
        signals.append("repair_attempt_present")
    if confusion_count >= 1:
        signals.append("initial_confusion_present")

    label = None
    if confusion_count >= 1 and repair_count >= 1 and (playful_count >= 1 or sexual_count >= 1):
        label = "playful_reengagement"
    elif confusion_count >= 1 and repair_count >= 1:
        label = "confusion_then_repair"
    elif sexual_count >= 1 and playful_count >= 1:
        label = "light_sexual_reciprocity"
    elif warm_count >= 2:
        label = "warm_receptivity"
    elif playful_count >= 1:
        label = "casual_flirtation"

    return {
        "connection_signals": signals,
        "connection_label": label,
        "confusion_count": confusion_count,
        "repair_count": repair_count,
        "playful_count": playful_count,
        "warm_count": warm_count,
        "sexual_count": sexual_count,
    }


def _extract_key_signals(text: str, domain_mode: str) -> Dict[str, Any]:
    t = _norm(text)
    signals: List[str] = []
    boundary_violations: List[str] = []

    money_terms = ["deposit", "lease agreement", "move in", "application fee", "rent", "$", "paid"]
    pressure_terms = ["urgent", "immediately", "right now", "must", "need to", "asap", "today"]
    sensitive_terms = ["ssn", "social security", "password", "login", "verification code", "otp", "pin"]

    if _contains_any(t, sensitive_terms):
        signals.append("credential_or_sensitive_info_signal")

    if _contains_any(t, pressure_terms):
        signals.append("pressure_present")

    if domain_mode == "dating_social" and _contains_any(t, ["come over", "hook up", "hookup", "sexy", "horny"]):
        signals.append("sexual_directness")

    if _contains_any(t, ["stop contacting me", "leave me alone", "not comfortable", "do not contact me"]):
        signals.append("boundary_language_present")

    if domain_mode == "housing_rental":
        if _contains_any(t, [
            "owner contact information once you're interested",
            "owner contact information once you are interested",
            "contact information once you're interested",
            "contact information once you are interested",
            "once you're interested in renting",
            "once you are interested in renting"
        ]):
            signals.append("withheld_owner_verification")
            boundary_violations.append("withheld_owner_verification")

        if _contains_any(t, [
            "other property", "wrong property", "belongs to a lady",
            "belongs to the lady", "initially talked about belongs to",
            "i think i sent you the other property"
        ]):
            signals.append("property_identity_shift")
            boundary_violations.append("property_identity_shift")

        if (
            _contains_any(t, ["out of town", "keys can be sent", "owner is currently out of town"])
            and _contains_any(t, ["manny", "private owner", "belongs to a lady", "other property"])
        ) or _contains_any(t, ["he's not out of town for work", "he is not out of town for work"]):
            signals.append("owner_identity_shift")
            boundary_violations.append("owner_identity_shift")

        if (
            _contains_any(t, ["application is approved", "once your application is approved"])
            and _contains_any(t, ["showing can be scheduled", "showing", "look at it", "see the property", "viewing"])
        ):
            signals.append("verification_path_shift")
            boundary_violations.append("verification_path_shift")

        if _contains_any(t, money_terms) and _contains_any(t, [
            "deposit is paid", "lease agreement signed", "move in",
            "would need the entire",
            "first month rent can be paid after your move in"
        ]):
            signals.append("payment_before_verification")
            boundary_violations.append("payment_before_verification")

        if _contains_any(t, ["deposit", "lease agreement", "move in", "application fee", "rent"]):
            signals.append("money_request")

    extraction_present = any(s in signals for s in [
        "money_request",
        "credential_or_sensitive_info_signal",
        "payment_before_verification"
    ])
    pressure_present = "pressure_present" in signals

    return {
        "signals": list(dict.fromkeys(signals)),
        "extraction_present": extraction_present,
        "pressure_present": pressure_present,
        "boundary_violations": list(dict.fromkeys(boundary_violations)),
    }


def _assign_lane(
    domain_mode: str,
    reciprocity_level: str,
    intent_horizon: str,
    extraction_present: bool,
    pressure_present: bool,
    boundary_violations: List[str],
    key_signals: List[str],
    relationship_type: str,
    text: str,
    connection_label: str = None,
) -> Dict[str, Any]:
    housing_cluster = sum(
        1 for s in key_signals if s in {
            "withheld_owner_verification",
            "property_identity_shift",
            "owner_identity_shift",
            "verification_path_shift",
            "payment_before_verification",
        }
    )

    if domain_mode == "housing_rental":
        if housing_cluster >= 2:
            return {"lane": "FRAUD", "primary_label": "transactional_extraction_pattern"}
        if _contains_any(text, ["wifi", "guest", "host", "parking", "check in", "during your stay"]) and housing_cluster == 0 and not extraction_present:
            return {"lane": "BENIGN", "primary_label": "routine_host_message"}

    if extraction_present and (pressure_present or housing_cluster >= 1):
        return {"lane": "FRAUD", "primary_label": "transactional_extraction_pattern"}

    if pressure_present and boundary_violations:
        return {"lane": "COERCION_RISK", "primary_label": "pressure_with_boundary_violation"}

    if domain_mode == "dating_social":
        if "sexual_directness" in key_signals and reciprocity_level == "HIGH" and not extraction_present and not pressure_present:
            return {"lane": "DATING_AMBIGUOUS", "primary_label": "fast_escalation_noncoercive"}
        return {"lane": "DATING_AMBIGUOUS", "primary_label": "mixed_intent"}

    if relationship_type in {"dating", "family", "friend"} and not extraction_present and not pressure_present:
        return {"lane": "RELATIONSHIP_NORMAL", "primary_label": "relationship_context"}

    if connection_label and not extraction_present and not pressure_present:
        return {"lane": "BENIGN", "primary_label": connection_label}

    return {"lane": "BENIGN", "primary_label": "routine_message"}


def _build_dampeners(
    domain_mode: str,
    reciprocity_level: str,
    intent_horizon: str,
    extraction_present: bool,
    pressure_present: bool,
    text: str,
    key_signals: List[str],
) -> List[str]:
    dampeners: List[str] = []

    if not extraction_present:
        dampeners.append("no_extraction")
    if not pressure_present:
        dampeners.append("no_pressure")
    if reciprocity_level == "HIGH":
        dampeners.append("high_reciprocity")
    if intent_horizon == "SHORT_TERM" and domain_mode == "dating_social":
        dampeners.append("short_term_alignment_noncoercive")

    housing_cluster = sum(
        1 for s in key_signals if s in {
            "withheld_owner_verification",
            "property_identity_shift",
            "owner_identity_shift",
            "verification_path_shift",
            "payment_before_verification",
        }
    )

    if domain_mode == "housing_rental" and _contains_any(text, ["wifi", "guest", "host", "parking", "check in", "during your stay"]) and housing_cluster == 0:
        dampeners.append("routine_transactional_context")

    return dampeners


def _risk_from_lane(
    lane: str,
    key_signals: List[str],
    key_dampeners: List[str],
    extraction_present: bool,
    pressure_present: bool,
) -> Dict[str, Any]:
    base = {
        "FRAUD": 82,
        "COERCION_RISK": 72,
        "DATING_AMBIGUOUS": 30,
        "RELATIONSHIP_NORMAL": 18,
        "BENIGN": 8,
    }[lane]

    bonuses = {
        "withheld_owner_verification": 8,
        "property_identity_shift": 8,
        "owner_identity_shift": 10,
        "verification_path_shift": 8,
        "payment_before_verification": 10,
        "money_request": 6,
        "credential_or_sensitive_info_signal": 10,
        "pressure_present": 6,
    }

    for s in key_signals:
        base += bonuses.get(s, 0)

    if "high_reciprocity" in key_dampeners:
        base -= 10
    if "no_extraction" in key_dampeners:
        base -= 8
    if "no_pressure" in key_dampeners:
        base -= 8
    if "routine_transactional_context" in key_dampeners:
        base -= 20

    if lane == "FRAUD":
        base = max(base, 75)

    if not extraction_present and not pressure_present and lane != "FRAUD":
        base = min(base, 35)

    score = max(0, min(100, base))

    if score >= 70:
        risk_level = "HIGH"
    elif score >= 35:
        risk_level = "MEDIUM"
    else:
        risk_level = "LOW"

    return {"risk_score": score, "risk_level": risk_level}


def _alternative_explanations(domain_mode: str, lane: str) -> List[str]:
    if lane == "FRAUD":
        return ["mismanaged transaction", "poor verification process"]
    if lane == "COERCION_RISK":
        return ["conflict escalation", "reactive communication"]
    if lane == "DATING_AMBIGUOUS":
        return ["mutual flirtation", "casual short-term framing", "playful escalation"]
    if domain_mode == "housing_rental":
        return ["routine host communication", "standard transactional logistics"]
    return ["low information", "ordinary conversation"]


def _confidence_score(lane: str, key_signals: List[str], key_dampeners: List[str]) -> float:
    score = 0.55
    score += min(len(key_signals) * 0.05, 0.25)
    score += min(len(key_dampeners) * 0.02, 0.10)

    if lane == "FRAUD":
        score += 0.08
    elif lane == "COERCION_RISK":
        score += 0.06

    return round(max(0.35, min(0.95, score)), 2)


def analyze_text(text: str, relationship_type: str = "stranger", context_note: str = "") -> Dict[str, Any]:
    normalized_text = (text or "").strip()

    domain = _detect_domain_mode(normalized_text)
    reciprocity_level = _detect_reciprocity(normalized_text)
    intent_horizon = _detect_intent_horizon(normalized_text, domain["domain_mode"])
    extracted = _extract_key_signals(normalized_text, domain["domain_mode"])
    connection_data = _detect_connection_signals(normalized_text)

    lane_info = _assign_lane(
        domain_mode=domain["domain_mode"],
        reciprocity_level=reciprocity_level,
        intent_horizon=intent_horizon,
        extraction_present=extracted["extraction_present"],
        pressure_present=extracted["pressure_present"],
        boundary_violations=extracted["boundary_violations"],
        key_signals=extracted["signals"],
        relationship_type=relationship_type,
        text=normalized_text,
        connection_label=connection_data["connection_label"],
    )

    dampeners = _build_dampeners(
        domain_mode=domain["domain_mode"],
        reciprocity_level=reciprocity_level,
        intent_horizon=intent_horizon,
        extraction_present=extracted["extraction_present"],
        pressure_present=extracted["pressure_present"],
        text=normalized_text,
        key_signals=extracted["signals"],
    )

    risk = _risk_from_lane(
        lane=lane_info["lane"],
        key_signals=extracted["signals"],
        key_dampeners=dampeners,
        extraction_present=extracted["extraction_present"],
        pressure_present=extracted["pressure_present"],
    )

    contradiction_signals = [
        {"type": s, "severity": "high"}
        for s in extracted["signals"]
        if s in {
            "withheld_owner_verification",
            "property_identity_shift",
            "owner_identity_shift",
            "verification_path_shift",
            "payment_before_verification",
        }
    ]

    narrative_integrity_score = max(0, 100 - (len(contradiction_signals) * 18))
    confidence = _confidence_score(lane_info["lane"], extracted["signals"], dampeners)

    if domain["domain_mode"] != "dating_social" or lane_info["lane"] in {"FRAUD", "COERCION_RISK"}:
        analysis_mode = "safety_only"
        interest_score = None
        interest_label = "Not Applicable"
    else:
        analysis_mode = "social_interest"
        interest_score = 55 if reciprocity_level == "HIGH" else 35
        interest_label = "Moderate" if reciprocity_level == "HIGH" else "Low"

    flags = extracted["signals"][:] if extracted["signals"] else ["No signals detected"]

    positive_signals = connection_data["connection_signals"][:]
    if reciprocity_level == "HIGH" and "reciprocal_engagement" not in positive_signals:
        positive_signals.append("reciprocal_engagement")

    if lane_info["lane"] == "BENIGN" and domain["domain_mode"] == "housing_rental":
        summary_logic = "Routine transactional hospitality/logistics message without extraction, pressure, or contradiction."
    elif lane_info["lane"] == "DATING_AMBIGUOUS":
        summary_logic = "Fast or mixed escalation is present, but the interaction lacks extraction and coercive pressure."
    elif lane_info["lane"] == "FRAUD":
        summary_logic = "Rental flow contains contradiction, withheld verification, and/or payment-before-verification structure."
    elif lane_info["lane"] == "COERCION_RISK":
        summary_logic = "Pressure plus boundary-related signals create coercion risk."
    else:
        summary_logic = "Conversation lacks strong danger criteria and defaults to a low-risk interpretation."

    return {
        "lane": lane_info["lane"],
        "primary_label": lane_info["primary_label"],
        "risk_level": risk["risk_level"],
        "risk_score": risk["risk_score"],
        "final_risk_score": risk["risk_score"],
        "confidence": confidence,
        "reciprocity_level": reciprocity_level,
        "intent_horizon": intent_horizon,
        "pressure_present": extracted["pressure_present"],
        "extraction_present": extracted["extraction_present"],
        "boundary_violations": extracted["boundary_violations"],
        "key_signals": extracted["signals"],
        "key_dampeners": dampeners,
        "alternative_explanations": _alternative_explanations(domain["domain_mode"], lane_info["lane"]),
        "summary_logic": summary_logic,
        "domain_mode": domain["domain_mode"],
        "domain_confidence": domain["domain_confidence"],
        "analysis_mode": analysis_mode,
        "contradiction_signals": contradiction_signals,
        "narrative_integrity_score": narrative_integrity_score,
        "risk_floor_applied": lane_info["lane"] == "FRAUD",
        "risk_floor_reason": "rental_contradiction_cluster" if lane_info["lane"] == "FRAUD" else None,
        "degraded": False,
        "flags": flags,
        "active_combos": [],
        "positive_signals": positive_signals,
        "vie_action": "BLOCK" if risk["risk_score"] >= 85 else ("WARN" if risk["risk_score"] >= 50 else ("MONITOR" if risk["risk_score"] >= 25 else "NONE")),
        "interest_score": interest_score,
        "interest_label": interest_label,
        "research_patch": {
            "style_markers": {
                "scope": "message_batch_only",
                "notes": ["Rebuild phase - style layer not yet reintroduced in deterministic form."]
            },
            "data_sufficiency": {
                "level": "medium" if len(normalized_text) >= 120 else "low",
                "reasons": [],
                "allowed_depth": "limited_inference" if len(normalized_text) >= 120 else "surface_only"
            }
        }
    }


def _turn_risk_score(text: str, relationship_type: str = "stranger") -> int:
    """Quick risk score for a single turn chunk. Used by analyze_turns."""
    result = analyze_text(text, relationship_type=relationship_type)
    return result.get("risk_score", 0)


def _turn_label(text: str, relationship_type: str = "stranger") -> str:
    """Quick primary label for a single turn chunk."""
    result = analyze_text(text, relationship_type=relationship_type)
    return result.get("primary_label", "routine_message")


def _arc_label(scores: List[int], labels: List[str]) -> Dict[str, Any]:
    """
    Compute conversation arc from per-turn scores and labels.

    Arc types:
    - escalating: risk score rises significantly across turns
    - de_escalating: risk score drops significantly
    - repair: starts high or confused, ends warm or low risk
    - flat_low: consistently low risk throughout
    - flat_high: consistently high risk throughout
    - volatile: large swings between turns
    - single_turn: only one turn provided
    """
    if len(scores) <= 1:
        return {
            "arc": "single_turn",
            "arc_label": "Single screenshot — upload more for pattern tracking",
            "direction": "neutral",
            "delta": 0,
        }

    first = scores[0]
    last = scores[-1]
    delta = last - first
    max_score = max(scores)
    min_score = min(scores)
    swing = max_score - min_score

    # Check for repair pattern — confusion/high early, warm/low late
    early_confused = any(l in {"routine_message", "confusion_then_repair", "playful_reengagement"}
                         for l in labels[:max(1, len(labels) // 2)])
    late_warm = any(l in {"warm_receptivity", "casual_flirtation", "playful_reengagement",
                          "light_sexual_reciprocity", "confusion_then_repair"}
                    for l in labels[len(labels) // 2:])

    if swing >= 30 and delta < -10:
        arc = "repair"
        arc_label = "Started rough, ended warmer — the conversation repaired itself"
        direction = "improving"
    elif delta >= 20:
        arc = "escalating"
        arc_label = "Risk is climbing across screenshots — something shifted"
        direction = "worsening"
    elif delta <= -20:
        arc = "de_escalating"
        arc_label = "Tension dropped as the conversation progressed"
        direction = "improving"
    elif swing >= 25:
        arc = "volatile"
        arc_label = "Inconsistent energy — the conversation keeps shifting"
        direction = "mixed"
    elif max_score >= 60:
        arc = "flat_high"
        arc_label = "Consistently elevated risk across all screenshots"
        direction = "concerning"
    elif early_confused and late_warm:
        arc = "repair"
        arc_label = "Started confused or defensive, ended warm — classic repair pattern"
        direction = "improving"
    else:
        arc = "flat_low"
        arc_label = "Low and stable — nothing escalated across these screenshots"
        direction = "neutral"

    return {
        "arc": arc,
        "arc_label": arc_label,
        "direction": direction,
        "delta": delta,
    }


def analyze_turns(
    text_chunks: List[str],
    relationship_type: str = "stranger",
) -> Dict[str, Any]:
    """
    Analyze a conversation as ordered turns (one chunk per screenshot).
    Returns per-turn scores plus an overall arc analysis.

    Args:
        text_chunks: List of OCR text strings, one per screenshot in order
        relationship_type: Relationship context

    Returns:
        Dict with turn_scores, arc, and summary
    """
    if not text_chunks:
        return {
            "turn_count": 0,
            "turns": [],
            "arc": "single_turn",
            "arc_label": "No screenshots provided",
            "direction": "neutral",
            "delta": 0,
            "multi_turn": False,
        }

    turns = []
    scores = []
    labels = []

    for i, chunk in enumerate(text_chunks):
        if not chunk.strip():
            continue

        result = analyze_text(chunk, relationship_type=relationship_type)
        score = result.get("risk_score", 0)
        label = result.get("primary_label", "routine_message")
        connection_data = _detect_connection_signals(chunk)

        scores.append(score)
        labels.append(label)

        # Human-readable turn summary
        if score >= 70:
            turn_verdict = "High concern"
            turn_color = "high"
        elif score >= 35:
            turn_verdict = "Worth watching"
            turn_color = "medium"
        else:
            turn_verdict = "Low concern"
            turn_color = "low"

        turns.append({
            "turn_number": i + 1,
            "label": label.replace("_", " "),
            "risk_score": score,
            "verdict": turn_verdict,
            "color": turn_color,
            "positive_signals": connection_data["connection_signals"],
            "key_signals": result.get("key_signals", []),
        })

    arc_data = _arc_label(scores, labels)

    return {
        "turn_count": len(turns),
        "turns": turns,
        "arc": arc_data["arc"],
        "arc_label": arc_data["arc_label"],
        "direction": arc_data["direction"],
        "delta": arc_data["delta"],
        "multi_turn": len(turns) > 1,
        "scores": scores,
    }
