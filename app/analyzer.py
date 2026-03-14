"""
analyzer.py - VibeLenz deterministic rules-based signal analyzer.

Design principles:
- Deterministic: same input always produces same output.
- Fail-closed: any internal error returns degraded=True, risk_score=100.
- No ML, no LLM, no external calls.
- All scoring logic is explicit and auditable.
"""

import logging
import re
from typing import Dict, List, Any

logger = logging.getLogger("vibelenz.analyzer")

# ---------------------------------------------------------------------------
# Signal definitions: (signal_id, pattern_list, score_weight, tier)
# tier: CRITICAL=40pts, HIGH=20pts, MEDIUM=10pts, LOW=5pts
# ---------------------------------------------------------------------------

SIGNALS = [
    # CRITICAL
    {
        "id": "financial_request",
        "tier": "CRITICAL",
        "weight": 40,
        "patterns": [
            r"\b(send|wire|transfer|pay|loan|lend|gift|invest)\b.{0,30}\b(money|cash|funds|dollars|usd|btc|bitcoin|crypto|gift\s?card|itunes|amazon card)\b",
            r"\b(gift\s?card|wire transfer|western union|moneygram|zelle|cashapp|venmo)\b",
            r"\bmy (bank account|routing number|wallet address)\b",
            r"\bneed.{0,20}(financial|money|funds|help paying)\b",
        ],
        "label": "Financial Request",
    },
    {
        "id": "credential_harvest",
        "tier": "CRITICAL",
        "weight": 40,
        "patterns": [
            r"\b(send|give|share|provide).{0,20}(password|login|otp|code|pin|ssn|social security)\b",
            r"\bverif(y|ication).{0,20}(code|otp|pin)\b",
            r"\bone[- ]time.{0,10}(code|password|pin)\b",
        ],
        "label": "Credential Harvest",
    },
    # HIGH
    {
        "id": "platform_shift",
        "tier": "HIGH",
        "weight": 20,
        "patterns": [
            r"\b(move|continue|talk|chat|text).{0,20}(telegram|whatsapp|signal|kik|snapchat|instagram|wechat|line app)\b",
            r"\b(telegram|whatsapp|signal|kik)\b.{0,20}\b(username|id|handle|contact)\b",
            r"\badd me on\b",
            r"\bcontact me (at|on|via)\b",
        ],
        "label": "Platform Shift",
    },
    {
        "id": "urgency_pressure",
        "tier": "HIGH",
        "weight": 20,
        "patterns": [
            r"\b(immediately|urgent|asap|right now|limited time|don.t wait|act now|hurry|expire|deadline)\b",
            r"\b(only|just).{0,10}(hour|minute|day|time).{0,10}left\b",
            r"\byou must.{0,20}(now|immediately|today|tonight)\b",
            r"\bdo (it|this) now\b",
        ],
        "label": "Urgency Pressure",
    },
    {
        "id": "isolation_tactic",
        "tier": "HIGH",
        "weight": 20,
        "patterns": [
            r"\b(don.t tell|keep.{0,10}secret|just between us|our little secret)\b",
            r"\b(don.t|do not).{0,20}(tell|show|share).{0,20}(family|friends|anyone|anybody|parents)\b",
            r"\bkeep this (private|between us|quiet)\b",
        ],
        "label": "Isolation / Secrecy",
    },
    # MEDIUM
    {
        "id": "emotional_manipulation",
        "tier": "MEDIUM",
        "weight": 10,
        "patterns": [
            r"\b(only you|no one else|you.re (special|different|the one))\b",
            r"\b(soul\s?mate|destiny|meant to be|love of my life)\b",
            r"\b(if you (loved|cared|trusted) me)\b",
            r"\b(prove.{0,20}love|show me.{0,20}trust)\b",
        ],
        "label": "Emotional Manipulation",
    },
    {
        "id": "identity_evasion",
        "tier": "MEDIUM",
        "weight": 10,
        "patterns": [
            r"\b(can.t (video|video call|facetime|cam)|camera (broken|not working))\b",
            r"\b(not in (the )?country|working (abroad|overseas)|military (deployment|base))\b",
            r"\b(oil rig|offshore|engineer (overseas|abroad))\b",
        ],
        "label": "Identity Evasion",
    },
    {
        "id": "investment_scam",
        "tier": "MEDIUM",
        "weight": 10,
        "patterns": [
            r"\b(guaranteed (profit|return|income)|risk.?free (investment|opportunity))\b",
            r"\b(crypto (trading|investment|platform)|forex (trading|signal))\b",
            r"\b(my (mentor|advisor|broker).{0,20}(invest|trading))\b",
            r"\b(double your money|10x return|100%.{0,10}profit)\b",
        ],
        "label": "Investment / Crypto Scam",
    },
    # LOW
    {
        "id": "excessive_affection",
        "tier": "LOW",
        "weight": 5,
        "patterns": [
            r"\b(miss you so much|thinking about you all day|can.t stop thinking)\b",
            r"\bi love you.{0,5}(already|so much|more than anything)\b",
            r"\byou.re (perfect|amazing|beautiful|gorgeous).{0,20}(never met anyone like)\b",
        ],
        "label": "Excessive Early Affection",
    },
    {
        "id": "sob_story",
        "tier": "LOW",
        "weight": 5,
        "patterns": [
            r"\b(sick|hospital|accident|surgery|emergency).{0,20}(money|help|funds|financial)\b",
            r"\bstranded.{0,20}(airport|hotel|country)\b",
            r"\b(robbed|stolen|lost (my|all).{0,20}(money|wallet|cards))\b",
        ],
        "label": "Sob Story / Emergency",
    },
]

# Score cap and confidence calibration
MAX_SCORE = 100
CONFIDENCE_BASE = 0.60


def _compile_patterns(patterns: List[str]) -> List[re.Pattern]:
    compiled = []
    for p in patterns:
        try:
            compiled.append(re.compile(p, re.IGNORECASE | re.DOTALL))
        except re.error as e:
            logger.warning(f"Pattern compile error: {e} | pattern: {p}")
    return compiled


# Pre-compile all patterns at module load
_COMPILED_SIGNALS = []
for sig in SIGNALS:
    _COMPILED_SIGNALS.append({
        **sig,
        "_compiled": _compile_patterns(sig["patterns"]),
    })


def analyze_text(text: str) -> Dict[str, Any]:
    """
    Deterministic risk analysis of extracted conversation text.

    Returns dict with: risk_score, flags, confidence, summary,
    recommended_action, degraded.

    Fails closed: any unhandled exception triggers degraded=True, score=100.
    """
    try:
        return _run_analysis(text)
    except Exception as e:
        logger.error(f"Analysis engine failure — failing closed: {e}")
        return {
            "risk_score": 100,
            "flags": ["ANALYSIS_ENGINE_FAILURE"],
            "confidence": 0.0,
            "summary": "Analysis engine encountered an error. Output blocked per fail-closed policy.",
            "recommended_action": "Do not proceed. Contact support.",
            "degraded": True,
        }


def _run_analysis(text: str) -> Dict[str, Any]:
    matched_signals: List[Dict] = []
    raw_score = 0

    for sig in _COMPILED_SIGNALS:
        hit = False
        for pattern in sig["_compiled"]:
            if pattern.search(text):
                hit = True
                break
        if hit:
            matched_signals.append({"id": sig["id"], "label": sig["label"], "tier": sig["tier"], "weight": sig["weight"]})
            raw_score += sig["weight"]

    # Cap score
    risk_score = min(raw_score, MAX_SCORE)

    # Build flags list (human-readable labels)
    flags = [s["label"] for s in matched_signals]

    # Confidence: scales with number of signals and tier severity
    critical_hits = sum(1 for s in matched_signals if s["tier"] == "CRITICAL")
    high_hits = sum(1 for s in matched_signals if s["tier"] == "HIGH")
    n_signals = len(matched_signals)

    if n_signals == 0:
        confidence = 0.50
    else:
        confidence = min(CONFIDENCE_BASE + (critical_hits * 0.12) + (high_hits * 0.06) + (n_signals * 0.02), 0.97)

    confidence = round(confidence, 2)

    # Summary generation
    summary = _build_summary(risk_score, matched_signals, flags)

    # Recommended action
    recommended_action = _build_recommendation(risk_score, matched_signals)

    return {
        "risk_score": risk_score,
        "flags": flags if flags else ["No signals detected"],
        "confidence": confidence,
        "summary": summary,
        "recommended_action": recommended_action,
        "degraded": False,
    }


def _build_summary(score: int, signals: List[Dict], flags: List[str]) -> str:
    if score == 0:
        return "No risk signals detected in this conversation."

    tier_counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for s in signals:
        tier_counts[s["tier"]] += 1

    parts = []
    if tier_counts["CRITICAL"] > 0:
        parts.append(f"{tier_counts['CRITICAL']} critical signal(s) including {_first_labels(signals, 'CRITICAL')}")
    if tier_counts["HIGH"] > 0:
        parts.append(f"{tier_counts['HIGH']} high-risk signal(s) including {_first_labels(signals, 'HIGH')}")
    if tier_counts["MEDIUM"] > 0:
        parts.append(f"{tier_counts['MEDIUM']} medium-risk signal(s)")
    if tier_counts["LOW"] > 0:
        parts.append(f"{tier_counts['LOW']} low-risk signal(s)")

    detail = "; ".join(parts) if parts else "multiple signals"

    if score >= 70:
        return f"High-risk conversation detected. Analysis found {detail}. Exercise extreme caution."
    elif score >= 40:
        return f"Medium-risk conversation. Analysis found {detail}. Proceed carefully."
    else:
        return f"Low-risk signals present. Analysis found {detail}. Monitor for escalation."


def _first_labels(signals: List[Dict], tier: str) -> str:
    labels = [s["label"] for s in signals if s["tier"] == tier][:2]
    return ", ".join(labels) if labels else "unknown"


def _build_recommendation(score: int, signals: List[Dict]) -> str:
    has_financial = any(s["id"] == "financial_request" for s in signals)
    has_credential = any(s["id"] == "credential_harvest" for s in signals)
    has_platform = any(s["id"] == "platform_shift" for s in signals)

    if score >= 70 or has_financial or has_credential:
        return (
            "Stop interaction immediately. Do not send money, gift cards, or any financial information. "
            "Do not share passwords or verification codes. Consider reporting this profile."
        )
    elif score >= 40:
        if has_platform:
            return "Do not move to a different platform. Keep communication on the original app where protections exist. Research this person's identity independently."
        return "Slow down this interaction. Verify the person's identity through a video call. Do not share personal or financial information."
    elif score > 0:
        return "Low risk signals noted. Remain cautious and trust your instincts. No immediate action required."
    else:
        return "No action required based on current analysis."
