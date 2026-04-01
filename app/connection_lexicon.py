from __future__ import annotations

"""
connection_lexicon.py — VibeLenz Connection Scoring v1.0
---------------------------------------------------------
Drop-in module. Call score_connection() after analyze_text().
Returns a connection_result dict ready for humanize_connection_result().

Pipeline position:
    raw text
        → analyze_text()          (risk/fraud detection)
        → score_connection()      (THIS MODULE — connection layer)
        → humanize_connection_result()  (presentation layer)

Kept minimal per v1.0 constraint. Do not expand lexicons or weights
until 50 real conversations and 10 paying users are in hand.
"""

from typing import Any, Dict, List, Tuple


# ---------------------------------------------------------------------------
# LEXICON
# ---------------------------------------------------------------------------

POSITIVE_CONNECTION_LEXICON: Dict[str, Dict[str, Any]] = {
    "affiliation": {
        "weight": 2.0,
        "terms": ["we", "us", "together", "both", "same", "ours"],
    },
    "emotional_warmth": {
        "weight": 2.5,
        "terms": [
            "like", "love", "appreciate", "enjoy", "glad",
            "happy", "excited", "care", "value", "miss",
        ],
    },
    "validation": {
        "weight": 1.8,
        "terms": [
            "agree", "exactly", "right", "true",
            "makes sense", "good point", "fair", "valid",
        ],
    },
    "engagement": {
        "weight": 2.2,
        "terms": [
            "tell me more", "what about", "how was",
            "i want to hear", "curious", "interesting",
        ],
    },
    "continuity_future": {
        "weight": 3.0,
        "terms": [
            "let's", "we should", "next time",
            "soon", "again", "later", "when are you free",
        ],
    },
    "softeners": {
        "weight": 0.8,
        "terms": [
            "honestly", "really", "just", "kind of",
            "a bit", "pretty", "actually",
        ],
    },
}

# Terms that negate a match immediately before a lexicon hit
NEGATIONS: List[str] = [
    "not", "don't", "dont", "never", "wasn't", "wasnt",
    "isn't", "isnt", "no", "without", "hardly", "barely",
]

# Extraction signals that hard-cap connection score
EXTRACTION_SIGNALS: List[str] = [
    "payment_before_verification",
    "money_request",
    "credential_or_sensitive_info_signal",
    "withheld_owner_verification",
]

# Score → label thresholds
_THRESHOLDS: List[Tuple[float, str]] = [
    (8.0, "HIGH"),
    (3.5, "MODERATE"),
    (0.0, "LOW"),
]


# ---------------------------------------------------------------------------
# INTERNAL HELPERS
# ---------------------------------------------------------------------------

def _is_negated(text: str, term: str) -> bool:
    """
    Return True if `term` appears in `text` immediately preceded by a negation
    within a 3-word window. E.g. "I don't like you" → negated.
    """
    idx = text.find(term)
    while idx != -1:
        prefix = text[:idx].split()
        window = prefix[-3:] if len(prefix) >= 3 else prefix
        if any(neg in window for neg in NEGATIONS):
            return True
        idx = text.find(term, idx + 1)
    return False


def _raw_score(text: str, lexicon: Dict[str, Dict[str, Any]]) -> Tuple[float, List[str]]:
    """
    Score a single text against the lexicon.
    Returns (score, list of matched category names).
    Negation filter applied per term.
    """
    score = 0.0
    matched_categories: List[str] = []
    lowered = text.lower()

    for category, data in lexicon.items():
        weight = float(data["weight"])
        hit = False
        for term in data["terms"]:
            if term in lowered and not _is_negated(lowered, term):
                score += weight
                hit = True
        if hit and category not in matched_categories:
            matched_categories.append(category)

    return score, matched_categories


def _apply_reciprocity(user_score: float, other_score: float) -> float:
    """
    Reciprocity gate: boost if both sides show connection signals,
    discount if only one side does.
    """
    if user_score > 0 and other_score > 0:
        return 1.2
    if user_score > 0 and other_score == 0:
        return 0.6
    return 1.0


def _extraction_present(analyzer_result: Dict[str, Any]) -> bool:
    """Check analyzer result for any extraction-class signals."""
    key_signals = analyzer_result.get("key_signals", []) or []
    flags = analyzer_result.get("flags", []) or []
    all_signals = set(key_signals) | set(flags)
    return bool(all_signals & set(EXTRACTION_SIGNALS))


def _label_from_score(score: float) -> str:
    for threshold, label in _THRESHOLDS:
        if score >= threshold:
            return label
    return "LOW"


def _confidence_from_score(score: float, reciprocal: bool) -> float:
    """
    Rough confidence estimate. Higher score + reciprocity = higher confidence.
    Kept simple for v1.0 — do not over-tune before real user data.
    """
    base = min(score / 15.0, 0.85)
    if reciprocal:
        base = min(base + 0.1, 0.90)
    return round(max(0.1, base), 2)


# ---------------------------------------------------------------------------
# PUBLIC API
# ---------------------------------------------------------------------------

def score_connection(
    user_text: str,
    other_text: str = "",
    analyzer_result: Dict[str, Any] = None,
    lexicon: Dict[str, Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Score connection signals in a conversation turn.

    Parameters
    ----------
    user_text       : Text from the user / perspective holder.
    other_text      : Text from the other party (used for reciprocity gate).
                      Pass empty string if not available.
    analyzer_result : Output dict from analyze_text(). Used to apply the
                      extraction override and read existing signals. Safe to
                      omit — scoring still runs without it.
    lexicon         : Override the default POSITIVE_CONNECTION_LEXICON.
                      Useful for testing or domain-specific tuning.

    Returns
    -------
    Dict with keys:
        connection_level   : "LOW" | "MODERATE" | "HIGH"
        raw_score          : float (pre-cap, pre-reciprocity)
        final_score        : float (post all adjustments)
        confidence         : float 0.0–1.0
        signals            : list of matched lexicon categories
        reciprocity        : bool — both sides showed connection signals
        extraction_capped  : bool — score was capped due to extraction signals
        connection_label   : str  — same as connection_level, for pipeline compat
    """
    if lexicon is None:
        lexicon = POSITIVE_CONNECTION_LEXICON
    if analyzer_result is None:
        analyzer_result = {}

    user_score, user_signals = _raw_score(user_text, lexicon)
    other_score, _ = _raw_score(other_text, lexicon) if other_text else (0.0, [])

    reciprocal = user_score > 0 and other_score > 0
    reciprocity_multiplier = _apply_reciprocity(user_score, other_score)
    adjusted = user_score * reciprocity_multiplier

    # Extraction override: hard cap at 2.0 if any extraction signal present
    extraction_cap = _extraction_present(analyzer_result)
    if extraction_cap:
        adjusted = min(adjusted, 2.0)

    label = _label_from_score(adjusted)
    confidence = _confidence_from_score(adjusted, reciprocal)

    return {
        "connection_level": label,
        "raw_score": round(user_score, 2),
        "final_score": round(adjusted, 2),
        "confidence": confidence,
        "signals": user_signals,
        "reciprocity": reciprocal,
        "extraction_capped": extraction_cap,
        "connection_label": label.lower(),   # pipeline compat with humanizer
    }


def merge_into_result(
    analyzer_result: Dict[str, Any],
    connection_result: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Merge connection_result into an analyzer_result dict in-place.
    Adds a top-level "connection" key and surfaces connection_level
    at the root for easy downstream access.
    """
    analyzer_result["connection"] = connection_result
    analyzer_result["connection_level"] = connection_result["connection_level"]
    analyzer_result["connection_signals"] = connection_result["signals"]
    return analyzer_result
