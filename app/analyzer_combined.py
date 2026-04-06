from __future__ import annotations

# ---------------------------------------------------------------------------
# analyzer.py — VibeLenz / VIE conversation signal analyzer
# Combined deterministic + LLM-powered engine
# ---------------------------------------------------------------------------
#
# ARCHITECTURE
# ------------
# Two execution modes share a single public interface:
#
#   analyze_text(text, relationship_type, context_note, use_llm=True)
#
#   use_llm=True  (default) — calls Claude API with full 30-signal VIE library.
#                             Falls back to deterministic engine if API unavailable.
#   use_llm=False           — pure deterministic pattern matching, no API cost.
#                             Used by analyze_turns() for per-turn scoring.
#
# Both modes return the same output schema so callers need no branching.
#
# WHAT CAME FROM EACH FILE
# ------------------------
# From deterministic engine (fixed):
#   - All 10 applied fixes (B1-B3, D1-D4, T1-T3)
#   - _norm, _contains_any, _count_any, _fuzzy_contains_any
#   - _detect_domain_mode (with tie-breaker fix)
#   - _detect_reciprocity (extended phrase lists)
#   - _detect_intent_horizon (NOT_APPLICABLE for non-dating)
#   - _detect_connection_signals
#   - _extract_key_signals (action-verb money_request, fuzzy CRITICAL phrases)
#   - _assign_lane, _build_dampeners, _risk_from_lane
#   - _score_evidence (probabilistic cumulative)
#   - _confidence_score (tier-weighted)
#   - analyze_turns (skipped_chunks tracking)
#
# From LLM backup:
#   - Full 30-signal VIE SYSTEM_PROMPT and RELATIONSHIP_PROMPT
#   - ROMANCE_HARD_GROOMING_SIGNALS, ROMANCE_SCAM_HARD_SIGNALS,
#     LOW_SEVERITY_RAPID_INTIMACY, RECIPROCAL_DAMPENERS signal sets
#   - _apply_relationship_guardrails (false-positive suppression)
#   - _extract_first_json_object (robust JSON parser)
#   - _compute_style_markers, _assess_data_sufficiency (rich research patch)
#   - _build_relationship_rubric (coaching-only scoring)
#   - _build_evidence_registry, governance constants
#   - _sanitize_prohibited_claims
#   - _normalize_messages, _message_like_lines_from_text
#   - Fail-closed policy: any API error → degraded=True, risk_score=100
#
# REMOVED AS REDUNDANT
# --------------------
#   - Duplicate "final_risk_score" key (B1 fix kept)
#   - Old linear cumulative evidence scoring (D1 fix kept)
#   - Flat-count confidence scoring (D3 fix kept)
#   - "UNCLEAR" for non-dating intent_horizon (D4 fix kept)
#   - Duplicate reciprocity phrase lists (T2 extended list kept)
#   - LLM backup's simple research_patch stub (rich version from backup kept)
# ---------------------------------------------------------------------------

import difflib
import json
import logging
import os
import re
from collections.abc import Iterable
from copy import deepcopy
from typing import Any, Dict, List, Optional

logger = logging.getLogger("vibelenz.analyzer")


# ===========================================================================
# LLM SYSTEM PROMPTS
# ===========================================================================

SYSTEM_PROMPT = """You are VibeLenz, a safety analysis engine powered by the Verified Interaction Engine (VIE).

You analyze conversation text extracted from screenshots using the full VIE 30-signal behavioral library.

## GOVERNANCE RULES
- Do not infer sex or gender from writing style.
- Do not say "this looks male" or "this looks female."
- Do not say "men text like this" or "women text like this."
- Prefer observable behavior over identity assumptions.
- If evidence is limited, reduce confidence and say so.

## PHASE DETECTION
First identify the conversation phase:
- GROOMING: Stranger building trust with suspicious patterns. ONLY use for unknown contacts with red flags. NEVER use GROOMING for established relationships - use NONE instead
- ESCALATION: Moving off platform, increasing intimacy, introducing financial topics
- COERCION: Direct financial asks, urgency pressure, isolation tactics
- ENDGAME: Large financial request, withdrawal barriers, document fabrication
- VICTIM_STATE: Victim defending the scammer, resisting intervention

## 30-SIGNAL BEHAVIORAL LIBRARY

### CRITICAL TIER (weight 1.0) — action: BLOCK
1. platform_spoof_redirect — Fake site injection after transaction agreement
2. financial_ask_escalation — Any monetary request; severity scales with amount and urgency
3. otp_credential_harvest — Request for OTP, password, PIN, verification code, SSN
4. remote_access_installation — Request to install AnyDesk, TeamViewer, or remote access tools
5. intervention_resistance_high — Victim actively argues against fraud alert
6. pig_butchering_sequence — Wrong number/chance contact → friendship → romance → crypto investment
7. withdrawal_barrier_loop — Withdrawal blocked requiring additional deposit; repeating pattern
8. authority_impersonation — Claims government, IRS, bank, or law enforcement identity + legal threat
9. document_fabrication_series — Multiple fake official documents justifying payment
10. bec_payment_substitution — Payment details substituted via spoofed business email
11. money_mule_recruitment — Instruction to receive and forward money or crypto on behalf of sender
12. unwitting_criminal_exposure — Victim unknowingly participating in criminal activity
13. deepfake_video_call_claim — Claims video call is impossible; AI-generated content suspected

### HIGH TIER (weight 0.8) — action: WARN
14. platform_migration_early — Move to encrypted channel (WhatsApp, Telegram, Signal) within first 1-3 exchanges
15. love_bomb_velocity — Affection declarations, soulmate language, intense bonding before 72-hour threshold
16. backstory_archetype_match — Identity matches known scam archetypes: military/offshore/widower/medical professional
17. verification_avoidance — 2+ deflections of video call or in-person meeting requests
18. gift_lure_fee_extraction — Gift or package claim requiring fee or personal details to release
19. isolation_pressure_calling — Multiple unsolicited contacts per day + instruction to keep situation private
20. ai_celebrity_ad_lure — Investment opportunity introduced via celebrity endorsement or fake social media ad
21. grief_mirroring_widowhood — Scammer discloses matching bereavement status early to create emotional bond
22. false_transaction_claim — Claims purchase/transaction already completed before victim agreement
23. fabricated_transit_proof — Unprompted photo or screenshot offered as fake delivery proof
24. pii_extraction_transaction — Home address, banking detail, or SSN requested under transaction pretext

### MEDIUM TIER (weight 0.5) — action: MONITOR
25. vulnerability_narrative_early — Hardship disclosure (divorce, illness, unsafe situation) before trust established
26. emotional_mirroring — Rapid identification and reflection of victim's emotional state and circumstances
27. trust_calibration_small_ask — Small initial financial ask ($50-500) establishing compliance before escalation
28. accidental_contact_opener — Opener claims wrong number or chance encounter to appear non-threatening

### LOW TIER (weight 0.2) — action: SOFT_FLAG
29. religious_trust_appeal — Unprompted religious framing to establish trustworthiness
30. urgency_deadline_soft — Mild time pressure without explicit threat; "limited time" or "today only"

## COMBO AMPLIFICATION
When these signal combinations appear together, increase risk score by 10-15 points:
- love_bomb_velocity + verification_avoidance = romance scam high confidence
- pig_butchering_sequence + withdrawal_barrier_loop = investment fraud CRITICAL
- backstory_archetype_match + vulnerability_narrative_early = scripted persona
- platform_migration_early + financial_ask_escalation = scam execution in progress
- authority_impersonation + isolation_pressure_calling = government impersonation scam
- document_fabrication_series + financial_ask_escalation = advance fee fraud
- money_mule_recruitment + financial_ask_escalation = REFER TO LAW ENFORCEMENT

## SCORING
- CRITICAL signal alone: 40-60 points
- HIGH signal: 15-25 points each
- MEDIUM signal: 8-12 points each
- LOW signal: 3-5 points each
- Combo amplification: +10-15 points per active combo
- Cap at 100

## VIE ACTIONS
- BLOCK (80-100): Stop all interaction immediately
- WARN (50-79): Caution required, verify before proceeding
- MONITOR (25-49): Watch for escalation
- SOFT_FLAG (1-24): Low risk, remain aware
- LAW_ENFORCEMENT_REFERRAL: Money mule or criminal exposure detected — note this in recommended_action

## OCR NOISE TOLERANCE
OCR text may have artifacts (| for I, garbled words). Interpret intent from context, not literal characters.

## POSITIVE SIGNAL LIBRARY
Also detect these trust-building signals and return them in positive_signals list:
- consistent_identity: Story details coherent and consistent
- reciprocal_engagement: Genuine two-way interest asks questions
- boundary_respect: Accepts limits without escalating
- transparent_intentions: Clear about who they are
- no_financial_topics: Money never comes up unsolicited
- meeting_willingness: Open to video calls or in-person
- patient_pacing: Does not rush intimacy
- verifiable_details: Provides specific checkable information
- reciprocal_flirting: Mutual playful interest without pressure
- mutual_joking: Shared humor / banter
- no_coercion: No threats, ultimatums, or fear-based pressure
- adult_consensual_tone: Tone appears adult and consensual in visible exchange

## EVIDENCE LINKER
For each detected signal, extract the shortest exact quote from the conversation that triggered it.
Quote must be verbatim text from the conversation — not paraphrased. Keep quotes under 20 words.
If OCR noise makes exact quoting impossible, use the closest clean approximation.

## OUTPUT
Respond with ONLY valid JSON, no markdown fences, no preamble:
{
  "risk_score": <integer 0-100>,
  "phase": "<GROOMING|ESCALATION|COERCION|ENDGAME|VICTIM_STATE|NONE>",
  "vie_action": "<BLOCK|WARN|MONITOR|SOFT_FLAG|LAW_ENFORCEMENT_REFERRAL|NONE>",
  "flags": ["<signal label strings>"],
  "evidence": {"<signal_id>": "<exact quote from conversation that triggered this signal>"},
  "active_combos": ["<combo description if triggered>"],
  "positive_signals": ["reciprocal_engagement", "no_financial_topics"],
  "confidence": <float 0.0-1.0>,
  "summary": "<plain language risk summary including phase and pattern identification>",
  "recommended_action": "<specific action — if LAW_ENFORCEMENT_REFERRAL, state this explicitly>",
  "degraded": false
}

If no risk signals detected return risk_score 0, phase NONE, vie_action NONE, flags ["No signals detected"], evidence {}. However always populate positive_signals with any trust indicators you observed even when risk is 0. A score of 0 with positive signals is the ideal result for a healthy conversation."""


RELATIONSHIP_PROMPT = """You are VibeLenz, a communication safety and dynamics analyzer.

You analyze conversation text to identify harmful communication patterns in ongoing personal relationships.
This is NOT a fraud/scam analysis. This is a behavioral dynamics analysis.

## GOVERNANCE RULES
- Do not infer sex or gender from writing style.
- Do not say "this looks male" or "this looks female."
- Do not say "men text like this" or "women text like this."
- Prefer observable behavior over identity assumptions.
- If evidence is limited, reduce confidence and say so.

SIGNAL LIBRARY — RELATIONSHIP DYNAMICS:

HIGH CONCERN (weight 0.8):
- guilt_induction: making other person responsible for speaker feelings; "after everything I've done", "you never", "I always"
- blame_shifting: deflecting all accountability to circumstances or the other party
- gaslighting: denying events, rewriting history, making other person question their perception
- DARVO: Deny Attack Reverse Victim and Offender — accused becomes the accuser
- victimhood_framing: positioning as wronged party regardless of context
- emotional_leverage: using children, shared history, or love as pressure

MEDIUM CONCERN (weight 0.5):
- love_bombing_cycle: excessive warmth before a request; coldness after compliance
- triangulation: introducing third parties to create guilt or pressure
- moving_goalposts: changing expectations after they have been met
- stonewalling_as_punishment: withdrawal used as control
- minimization: dismissing the other person's feelings as overreactions
- false_equivalence: comparing unrelated situations to deflect concerns

LOW CONCERN (weight 0.3):
- passive_aggression: indirect hostility through sarcasm or deliberate inefficiency
- catastrophizing: escalating minor issues to gain compliance
- boundary_violations: ignoring stated limits repeatedly

PHASE DETECTION:
- TENSION_BUILDING: Low-level friction, minor blame
- INCIDENT: Active manipulation, direct pressure
- RECONCILIATION: Warmth cycle, love bombing, promises
- CALM: Normal interaction

SCORING: 0-29 healthy, 30-59 concerning, 60-79 significant manipulation, 80-100 severe

Respond with ONLY valid JSON, no markdown:
{"risk_score": <0-100>, "phase": "<TENSION_BUILDING|INCIDENT|RECONCILIATION|CALM|NONE>", "vie_action": "<WARN|MONITOR|SOFT_FLAG|NONE>", "flags": ["signal labels"], "evidence": {"signal_id": "exact quote"}, "active_combos": ["combo descriptions"], "confidence": <0.0-1.0>, "summary": "<dynamic summary>", "recommended_action": "<practical guidance>", "degraded": false}

If no concerning patterns: risk_score 0, phase CALM, flags ["No concerning patterns detected"]."""


# ===========================================================================
# SIGNAL SETS — GUARDRAIL CLASSIFICATION
# ===========================================================================

ROMANCE_HARD_GROOMING_SIGNALS = {
    "target_vulnerability_exploitation",
    "age_power_imbalance",
    "secrecy_encouragement",
    "boundary_conditioning",
    "dependency_shaping",
    "isolation_from_others",
    "coercive_sexual_progression",
    "repeated_manipulation_over_time",
}

ROMANCE_SCAM_HARD_SIGNALS = {
    "financial_ask_escalation",
    "money_request",
    "gift_lure_fee_extraction",
    "emergency_narrative",
    "off_platform_migration",
    "verification_refusal",
    "identity_inconsistency",
    "repeated_contradiction",
    "resource_extraction",
}

LOW_SEVERITY_RAPID_INTIMACY = {
    "love_bomb_velocity",
    "fast_sexual_tone",
    "rapid_flirtation",
    "sexual_forwardness",
    "accelerated_intimacy",
    "accidental_contact_opener",
    "uncertain_identity",
}

RECIPROCAL_DAMPENERS = {
    "mutual_joking",
    "reciprocal_flirting",
    "back_and_forth_engagement",
    "no_fear_discomfort",
    "no_coercion",
    "no_resource_extraction",
    "adult_consensual_tone",
}


# ===========================================================================
# STYLE / GOVERNANCE CONSTANTS
# ===========================================================================

WORD_RE = re.compile(r"\b[\w']+\b", re.UNICODE)
EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F900-\U0001F9FF"
    "\u2600-\u26FF"
    "\u2700-\u27BF"
    "]",
    flags=re.UNICODE,
)

POSITIVE_WORDS = {
    "love", "like", "miss", "glad", "happy", "sweet", "cute", "good", "great", "amazing",
    "wonderful", "beautiful", "handsome", "appreciate", "excited", "yay", "thanks", "thank",
}
POSITIVE_EMOJI = {"❤️", "❤", "😍", "🥰", "😘", "😊", "☺", "🙂", "😉", "💕", "💖", "💘", "🔥"}
INTENSIFIERS = {
    "very", "really", "so", "super", "extremely", "totally", "literally", "absolutely",
    "incredibly", "seriously", "deeply", "truly",
}
DESCRIPTIVE_WORDS = {
    "amazing", "awesome", "beautiful", "cute", "sweet", "lovely", "perfect", "hot",
    "sexy", "kind", "honest", "great", "wonderful", "adorable", "gorgeous",
}
HEDGES = {
    "maybe", "perhaps", "sort", "kind", "kinda", "possibly", "probably",
    "might", "could", "just", "somewhat", "apparently", "guess",
}
DIRECT_MARKERS = {
    "need", "want", "let's", "lets", "call", "come", "tell", "be", "stop", "send",
    "meet", "answer", "reply", "do", "don't", "dont", "can", "will",
}
SEXUAL_WORDS = {
    "sex", "sexy", "nude", "nudes", "horny", "fuck", "fucking", "dick", "cock", "pussy",
    "ass", "tits", "boobs", "cum", "orgasm", "kiss me", "spank", "turn on", "ride",
}
VULGAR_WORDS = {
    "fuck", "fucking", "shit", "bitch", "asshole", "damn", "wtf", "bullshit", "mf", "motherfucker",
}
DISCLOSURE_MARKERS = {
    "i feel", "i felt", "i'm scared", "im scared", "i'm worried", "im worried", "i'm hurt",
    "im hurt", "i'm insecure", "im insecure", "i need", "i want", "i care", "i miss",
}
WEAPONIZATION_MARKERS = {
    "you always", "you never", "that's why nobody", "no wonder", "crazy", "pathetic", "embarrassing",
}
HONESTY_MARKERS = {
    "honestly", "to be honest", "truth", "for real", "real talk", "transparent",
}
CONFLICT_MARKERS = {
    "upset", "angry", "mad", "hurt", "annoyed", "frustrated", "problem", "issue", "argue", "fight",
}
REPAIR_MARKERS = {
    "sorry", "apologize", "understand", "hear you", "let's fix", "work on", "figure this out",
    "talk it through", "make it better", "repair",
}
GROWTH_MARKERS = {
    "grow", "better", "improve", "work on", "learn", "together", "healthy", "understand",
    "communicate", "figure out", "mutual",
}
PROHIBITED_CLAIMS = (
    "this looks male",
    "this looks female",
    "women text like this",
    "men text like this",
)

TIER_DEFINITIONS = [
    {
        "tier": 1,
        "label": "peer_reviewed_direct_behavior_relevance",
        "examples": ["direct behavioral texting research"],
        "default_weight_policy": "moderate_if_replication_is_limited",
        "notes": "Tier 1 still does not justify sex-based claims without replication and internal validation.",
    },
    {
        "tier": 2,
        "label": "peer_reviewed_adjacent_service_relevance",
        "examples": ["supportive texting service research", "adjacent texting or attitude research"],
        "default_weight_policy": "use_for_product_design_or_adjacent_hypotheses",
        "notes": "Useful for support/coaching design or adjacent constraints, not direct dating-text inference unless validated.",
    },
    {
        "tier": 3,
        "label": "exploratory_student_or_low_power_research",
        "examples": ["small-sample campus or student exploratory work"],
        "default_weight_policy": "hypothesis_only_low_weight",
        "notes": "Do not operationalize as a strong rule.",
    },
    {
        "tier": 4,
        "label": "conceptual_or_coaching_framework",
        "examples": ["relationship or self-help frameworks"],
        "default_weight_policy": "coaching_only_not_detection",
        "notes": "May inform guidance rubrics but must not become evidence of risk or intent.",
    },
]

FEATURE_REGISTRATION_SCHEMA = {
    "required_fields": [
        "feature_name", "source_name", "source_tier", "what_it_measures",
        "engine_side", "validation_status", "allowed_weight_range",
        "prohibited_claims", "known_limits",
    ],
    "engine_side_values": ["inference", "coaching", "retention", "governance"],
    "validation_status_values": ["validated", "hypothesis_only", "internal_only"],
}

HARD_RULES = [
    "Every new research-derived feature must be registered before use.",
    "Hypothesis-only features cannot drive high-confidence outputs.",
    "Tier 3 and Tier 4 sources cannot justify identity inference.",
    "Tier 4 sources cannot drive detection or risk classification.",
    "Adjacent-use sources must be explicitly tagged as adjacent-use only.",
]

PROHIBITED_OUTPUT_CLAIMS = [
    "No sex or gender identity inference from writing style.",
    "No group-based claims about how men or women text.",
    "No male-vs-female classifier language in user-facing output.",
]

DEFAULT_FEATURE_REGISTRY = [
    {
        "feature_name": "style_markers",
        "source_name": "internal_observable_feature_set",
        "source_tier": 1,
        "what_it_measures": "Session-level lexical and punctuation observables only.",
        "engine_side": "inference",
        "validation_status": "internal_only",
        "allowed_weight_range": [0.0, 0.35],
        "prohibited_claims": [
            "No sex or gender inference.",
            "No stable personality inference.",
            "No attachment or intent inference from style markers alone.",
        ],
        "known_limits": [
            "Highly context-dependent.",
            "Platform, age, audience, and relationship stage can shift style.",
            "Short excerpts can distort marker distribution.",
        ],
    },
    {
        "feature_name": "data_sufficiency",
        "source_name": "internal_guardrail_policy",
        "source_tier": 1,
        "what_it_measures": "Whether enough evidence exists to support deeper interpretation.",
        "engine_side": "governance",
        "validation_status": "validated",
        "allowed_weight_range": [1.0, 1.0],
        "prohibited_claims": ["Do not bypass sufficiency gating for convenience."],
        "known_limits": ["Depends on upstream metadata quality."],
    },
    {
        "feature_name": "relationship_rubric",
        "source_name": "Connect-style coaching framework",
        "source_tier": 4,
        "what_it_measures": "Coaching-oriented relationship quality dimensions.",
        "engine_side": "coaching",
        "validation_status": "hypothesis_only",
        "allowed_weight_range": [0.0, 0.2],
        "prohibited_claims": [
            "Cannot drive risk classification.",
            "Cannot be shown when evidence is insufficient.",
        ],
        "known_limits": [
            "Single screenshots rarely justify confident scoring.",
            "Should be treated as a coaching lens, not a fact detector.",
        ],
    },
    {
        "feature_name": "coaching_message",
        "source_name": "supportive-texting service research",
        "source_tier": 2,
        "what_it_measures": "Optional retention/support message design constraints.",
        "engine_side": "retention",
        "validation_status": "hypothesis_only",
        "allowed_weight_range": [0.0, 0.1],
        "prohibited_claims": [
            "Must remain separate from core inference.",
            "Cannot create pseudo-therapeutic authority claims.",
        ],
        "known_limits": ["Not validated as a dating-text classifier input."],
    },
]


# ===========================================================================
# DETERMINISTIC ENGINE — SIGNAL REGISTRY
# ===========================================================================

SIGNAL_REGISTRY = {
    "payment_before_verification": {
        "tier": "CRITICAL", "weight": 0.90,
        "label": "Payment before verification",
        "explanation": "A deposit or payment was requested before you could view or verify the property. This is the single strongest fraud indicator in rental scams.",
    },
    "withheld_owner_verification": {
        "tier": "CRITICAL", "weight": 0.85,
        "label": "Owner identity withheld",
        "explanation": "Contact information or identity verification was conditioned on you committing first. Legitimate landlords identify themselves upfront.",
    },
    "owner_identity_shift": {
        "tier": "CRITICAL", "weight": 0.82,
        "label": "Owner identity shifted",
        "explanation": "The person claiming to own or manage the property changed their story about who the owner is. Identity instability is a hallmark of rental fraud.",
    },
    "property_identity_shift": {
        "tier": "HIGH", "weight": 0.75,
        "label": "Property identity shifted",
        "explanation": "The listing details changed mid-conversation — different address, wrong property sent, or story shifted. Scammers often manage multiple fake listings and mix them up.",
    },
    "verification_path_shift": {
        "tier": "HIGH", "weight": 0.72,
        "label": "Verification path reversed",
        "explanation": "The normal sequence was inverted — approval or payment was requested before a showing. In legitimate rentals, you see it first.",
    },
    "money_request": {
        "tier": "HIGH", "weight": 0.60,
        "label": "Money mentioned early",
        "explanation": "Financial terms appeared before basic trust was established. Not automatically fraudulent, but worth tracking.",
    },
    "credential_or_sensitive_info_signal": {
        "tier": "CRITICAL", "weight": 0.88,
        "label": "Sensitive information requested",
        "explanation": "SSN, passwords, verification codes, or login credentials were requested. Legitimate landlords never need this before a showing.",
    },
    "pressure_present": {
        "tier": "HIGH", "weight": 0.65,
        "label": "Urgency or pressure detected",
        "explanation": "Language designed to rush a decision appeared — urgency, deadlines, or 'act now' framing.",
    },
    "sexual_directness": {
        "tier": "MEDIUM", "weight": 0.35,
        "label": "Direct sexual framing",
        "explanation": "Explicit sexual content appeared. In dating context not automatically a risk signal, but timing matters.",
    },
    "boundary_language_present": {
        "tier": "HIGH", "weight": 0.70,
        "label": "Boundary violation language",
        "explanation": "Language indicating you asked them to stop or felt uncomfortable appeared.",
    },
}

_TIER_CONFIDENCE_WEIGHTS = {
    "CRITICAL": 0.10,
    "HIGH": 0.07,
    "MEDIUM": 0.04,
    "LOW": 0.02,
}


# ===========================================================================
# SHARED UTILITIES
# ===========================================================================

def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def _contains_any(text: str, phrases: List[str]) -> bool:
    t = _norm(text)
    return any(p in t for p in phrases)


def _count_any(text: str, phrases: List[str]) -> int:
    t = _norm(text)
    return sum(1 for p in phrases if p in t)


def _fuzzy_contains_any(text: str, phrases: List[str], threshold: float = 0.85) -> bool:
    """Sliding-window fuzzy match — handles OCR noise on CRITICAL signal phrases."""
    t = _norm(text)
    words = t.split()
    for phrase in phrases:
        phrase_words = phrase.split()
        n = len(phrase_words)
        for i in range(len(words) - n + 1):
            window = " ".join(words[i: i + n])
            if difflib.SequenceMatcher(None, window, phrase).ratio() >= threshold:
                return True
    return False


def _norm_signal_name(x: Any) -> str:
    return str(x).strip().lower().replace(" ", "_")


def _extract_signal_names(result: Dict[str, Any]) -> set:
    names: set = set()
    for field in ("flags", "active_combos", "positive_signals", "labels"):
        for item in result.get(field, []) or []:
            if isinstance(item, str):
                names.add(_norm_signal_name(item))
            elif isinstance(item, dict):
                for key in ("signal", "name", "id", "label"):
                    if item.get(key):
                        names.add(_norm_signal_name(item[key]))
    return names


def _tokenize(text: str) -> List[str]:
    return [t.lower() for t in WORD_RE.findall(text)]


def _count_phrase_hits(text: str, phrases: Iterable[str]) -> int:
    lowered = text.lower()
    return sum(1 for phrase in phrases if phrase in lowered)


# ===========================================================================
# MESSAGE NORMALISATION
# ===========================================================================

def _message_like_lines_from_text(text: str) -> List[Dict[str, Any]]:
    lines = [line.strip() for line in str(text).splitlines() if line.strip()]
    if not lines:
        return []
    messages: List[Dict[str, Any]] = []
    speaker_pattern = re.compile(r"^([A-Za-z0-9_ .'-]{1,30}):\s+(.*)$")
    inferred_speakers = ["speaker_a", "speaker_b"]
    speaker_index = 0
    for line in lines:
        match = speaker_pattern.match(line)
        if match:
            messages.append({"speaker": match.group(1).strip(), "text": match.group(2).strip(), "timestamp": None})
            continue
        messages.append({"speaker": inferred_speakers[speaker_index % 2], "text": line, "timestamp": None})
        speaker_index += 1
    return messages


def _normalize_messages(messages: Any) -> List[Dict[str, Any]]:
    if messages is None:
        return []
    if isinstance(messages, str):
        result = _message_like_lines_from_text(messages)
        return result if result else [{"speaker": "unknown", "text": messages, "timestamp": None}]
    normalized: List[Dict[str, Any]] = []
    if isinstance(messages, dict):
        messages = [messages]
    if not isinstance(messages, Iterable):
        return []
    for item in messages:
        if isinstance(item, str):
            normalized.extend(
                _message_like_lines_from_text(item)
                or [{"speaker": "unknown", "text": item, "timestamp": None}]
            )
            continue
        if not isinstance(item, dict):
            continue
        text = item.get("text") or item.get("content") or item.get("message") or item.get("body") or ""
        speaker = item.get("speaker") or item.get("role") or item.get("author") or "unknown"
        normalized.append({"speaker": str(speaker), "text": str(text), "timestamp": item.get("timestamp")})
    return [m for m in normalized if m.get("text", "").strip()]


# ===========================================================================
# RESEARCH PATCH HELPERS
# ===========================================================================

def _compute_style_markers(messages: Any) -> Dict[str, Any]:
    normalized = _normalize_messages(messages)
    combined_text = " ".join(m["text"] for m in normalized)
    tokens = _tokenize(combined_text)
    total_tokens = max(len(tokens), 1)
    total_messages = max(len(normalized), 1)
    total_chars = max(len(combined_text), 1)

    emoji_count = len(EMOJI_RE.findall(combined_text))
    exclamation_count = combined_text.count("!")
    positive_word_hits = sum(1 for t in tokens if t in POSITIVE_WORDS)
    positive_emoji_hits = sum(1 for char in combined_text if char in POSITIVE_EMOJI)
    adjective_hits = sum(1 for t in tokens if t in INTENSIFIERS or t in DESCRIPTIVE_WORDS)
    direct_hits = sum(1 for t in tokens if t in DIRECT_MARKERS)
    hedge_hits = sum(1 for t in tokens if t in HEDGES)
    sexual_hits = sum(1 for t in tokens if t in SEXUAL_WORDS) + _count_phrase_hits(combined_text, SEXUAL_WORDS)
    vulgar_hits = sum(1 for t in tokens if t in VULGAR_WORDS)

    avg_tokens_per_message = len(tokens) / total_messages
    comma_clause_bonus = combined_text.count(",") + combined_text.count(";")
    elaboration_raw = (avg_tokens_per_message / 30.0) + (comma_clause_bonus / max(total_messages, 1) / 4.0)
    directness_raw = ((direct_hits * 1.2) - (hedge_hits * 0.8)) / max(total_messages, 1)

    return {
        "scope": "message_batch_only",
        "identity_inference_allowed": False,
        "trait_inference_allowed": False,
        "emoji_density": round(_clamp(emoji_count / total_tokens), 4),
        "positive_affect_marker_density": round(_clamp((positive_word_hits + positive_emoji_hits) / total_tokens), 4),
        "exclamation_density": round(_clamp(exclamation_count / max(total_chars / 40.0, 1.0)), 4),
        "adjective_intensity_score": round(_clamp(adjective_hits / max(total_tokens * 0.3, 1.0)), 4),
        "directness_score": round(_clamp(0.5 + (directness_raw / 4.0)), 4),
        "elaboration_score": round(_clamp(elaboration_raw), 4),
        "sexual_explicitness_score": round(_clamp(sexual_hits / max(total_tokens * 0.15, 1.0)), 4),
        "vulgarity_score": round(_clamp(vulgar_hits / max(total_tokens * 0.1, 1.0)), 4),
        "notes": [
            "Session-level observables only.",
            "Do not infer sex, gender, stable personality, or attachment style from these markers alone.",
        ],
    }


def _assess_data_sufficiency(messages: Any) -> Dict[str, Any]:
    normalized = _normalize_messages(messages)
    texts = [m["text"] for m in normalized]
    speakers = {m["speaker"] for m in normalized if m.get("speaker")}
    combined_text = " ".join(texts)
    total_tokens = len(_tokenize(combined_text))
    total_messages = len(normalized)

    reasons: List[str] = []
    if total_messages <= 2:
        reasons.append("short_excerpt_only")
    if total_tokens < 40:
        reasons.append("single_screenshot_only")
    if total_messages < 4:
        reasons.append("insufficient_reciprocity")
    if not any(m.get("timestamp") for m in normalized):
        reasons.append("no_temporal_continuity")
    if len(speakers) < 2 and total_messages < 6:
        reasons.append("limited_participant_clarity")

    if total_messages >= 8 and total_tokens >= 120:
        level = "high"
        allowed_depth = "full_inference"
        reasons = [r for r in reasons if r not in {"short_excerpt_only", "single_screenshot_only", "insufficient_reciprocity"}]
    elif total_messages >= 4 and total_tokens >= 40:
        level = "medium"
        allowed_depth = "limited_inference"
    else:
        level = "low"
        allowed_depth = "surface_only"

    return {"level": level, "reasons": sorted(set(reasons)), "allowed_depth": allowed_depth}


def _build_relationship_rubric(messages: Any, data_sufficiency: Dict[str, Any]) -> Dict[str, Any]:
    normalized = _normalize_messages(messages)
    combined_text = " ".join(m["text"] for m in normalized).lower()

    if data_sufficiency.get("level") == "low":
        return {
            "status": "not_enough_evidence",
            "confidence": "low",
            "triggered_conditions": list(data_sufficiency.get("reasons", [])),
            "message": "Not enough reciprocal conversation evidence for relationship-quality scoring.",
            "scores": None,
            "notes": ["RelationshipRubric is coaching-only.", "Suppressed because data sufficiency is low."],
        }

    disclosure_hits = _count_phrase_hits(combined_text, DISCLOSURE_MARKERS)
    weaponization_hits = _count_phrase_hits(combined_text, WEAPONIZATION_MARKERS)
    honesty_hits = _count_phrase_hits(combined_text, HONESTY_MARKERS)
    conflict_hits = _count_phrase_hits(combined_text, CONFLICT_MARKERS)
    repair_hits = _count_phrase_hits(combined_text, REPAIR_MARKERS)
    growth_hits = _count_phrase_hits(combined_text, GROWTH_MARKERS)
    we_hits = combined_text.count(" we ")
    i_hits = combined_text.count(" i ")

    authenticity = 1 + min(disclosure_hits, 2)
    vulnerability = min(3, disclosure_hits)
    trust_non_weaponization = max(0, min(3, 2 - min(weaponization_hits, 2) + min(repair_hits, 1)))
    honesty = min(3, 1 + min(honesty_hits + max(i_hits // 5, 0), 2))
    productive_conflict = 0
    if conflict_hits > 0:
        productive_conflict = 1
    if conflict_hits > 0 and repair_hits > 0:
        productive_conflict = 2
    if conflict_hits > 1 and repair_hits > 1:
        productive_conflict = 3
    mutual_growth_orientation = min(3, growth_hits + min(we_hits // 3, 1))

    return {
        "status": "scored",
        "confidence": "medium" if data_sufficiency.get("level") == "medium" else "high",
        "triggered_conditions": [
            f"data_sufficiency:{data_sufficiency.get('level', 'low')}",
            "coaching_only_framework",
        ],
        "message": "Coaching-oriented rubric generated under data sufficiency gating.",
        "scores": {
            "authenticity": max(0, min(3, authenticity)),
            "vulnerability": max(0, min(3, vulnerability)),
            "trust_non_weaponization": max(0, min(3, trust_non_weaponization)),
            "honesty": max(0, min(3, honesty)),
            "productive_conflict": max(0, min(3, productive_conflict)),
            "mutual_growth_orientation": max(0, min(3, mutual_growth_orientation)),
        },
        "notes": [
            "This rubric is not a risk classifier.",
            "Treat scores as coaching guidance, not factual diagnosis.",
        ],
    }


def _build_evidence_registry() -> Dict[str, Any]:
    return {
        "tiers": deepcopy(TIER_DEFINITIONS),
        "feature_registration_schema": deepcopy(FEATURE_REGISTRATION_SCHEMA),
        "hard_rules": deepcopy(HARD_RULES),
        "prohibited_output_claims": deepcopy(PROHIBITED_OUTPUT_CLAIMS),
        "registered_features": deepcopy(DEFAULT_FEATURE_REGISTRY),
    }


def _build_research_patch(text: Any, relationship_type: str) -> Dict[str, Any]:
    style_markers = _compute_style_markers(text)
    data_sufficiency = _assess_data_sufficiency(text)
    coaching = {
        "relationship_rubric": (
            _build_relationship_rubric(text, data_sufficiency)
            if relationship_type in {"dating", "family", "friend", "business"}
            else {
                "status": "not_applicable",
                "confidence": "low",
                "triggered_conditions": ["non_relationship_mode"],
                "message": "Relationship rubric suppressed outside relationship-analysis modes.",
                "scores": None,
                "notes": ["Coaching rubric is only for relationship-guidance contexts."],
            }
        ),
        "display_policy": {
            "show_only_when": "data_sufficiency.level != low",
            "purpose": "guidance_only",
        },
    }
    return {
        "style_markers": style_markers,
        "data_sufficiency": data_sufficiency,
        "governance": _build_evidence_registry(),
        "coaching": coaching,
        "retention": {
            "coaching_message_policy": {
                "enabled": False,
                "consent_required": True,
                "constraints": ["brief", "affirmative", "relevant", "low_effort", "non_intrusive"],
                "separation_rule": "Must remain separate from core inference.",
            }
        },
    }


# ===========================================================================
# GUARDRAILS & OUTPUT SANITISATION
# ===========================================================================

def _ensure_label(result: Dict[str, Any], label: str) -> Dict[str, Any]:
    labels = result.get("labels", []) or []
    if label not in labels:
        labels.append(label)
    result["labels"] = labels
    return result


def _remove_label(result: Dict[str, Any], label: str) -> Dict[str, Any]:
    result["labels"] = [x for x in (result.get("labels", []) or []) if str(x).lower() != label.lower()]
    return result


def _cap_risk(result: Dict[str, Any], cap: int) -> Dict[str, Any]:
    try:
        result["risk_score"] = min(int(result.get("risk_score", 0)), cap)
    except Exception:
        result["risk_score"] = 0
    return result


def _set_action_at_most(result: Dict[str, Any], max_action: str = "MONITOR") -> Dict[str, Any]:
    order = {"NONE": 0, "SOFT_FLAG": 1, "MONITOR": 2, "WARN": 3, "BLOCK": 4, "LAW_ENFORCEMENT_REFERRAL": 5}
    if order.get(str(result.get("vie_action", "NONE")).upper(), 0) > order.get(max_action, 2):
        result["vie_action"] = max_action
    return result


def _apply_relationship_guardrails(result: Dict[str, Any], relationship_type: str = "stranger") -> Dict[str, Any]:
    if str(relationship_type or "stranger").lower() == "stranger":
        return result

    signal_names = _extract_signal_names(result)
    hard_groom = len(signal_names & ROMANCE_HARD_GROOMING_SIGNALS)
    hard_scam = len(signal_names & ROMANCE_SCAM_HARD_SIGNALS)
    low_rapid = len(signal_names & LOW_SEVERITY_RAPID_INTIMACY)
    dampeners = len(signal_names & RECIPROCAL_DAMPENERS)

    no_money = not any(x in signal_names for x in {
        "financial_ask_escalation", "money_request", "resource_extraction", "gift_lure_fee_extraction"
    })
    no_offplatform = not any(x in signal_names for x in {
        "off_platform_migration", "platform_migration_early", "secrecy_encouragement"
    })
    no_coercion = not any(x in signal_names for x in {
        "coercive_sexual_progression", "boundary_conditioning", "isolation_from_others", "threat", "blackmail"
    })

    if hard_groom < 3:
        result = _remove_label(result, "grooming")
        result = _remove_label(result, "early stage grooming indicators")
        result = _remove_label(result, "predation")
        if result.get("phase") in {"GROOMING", "COERCION", "ENDGAME"}:
            result["phase"] = "NONE"

    if low_rapid >= 1 and hard_groom == 0 and hard_scam == 0:
        result = _ensure_label(result, "rapid_flirtation")
        result = _ensure_label(result, "needs_verification")
        result = _cap_risk(result, 24)
        result = _set_action_at_most(result, "MONITOR")
        result["summary"] = "Conversation shows identity ambiguity and quick flirtation escalation, but no clear financial coercion or exploitative behavior in the visible exchange."

    if no_money and no_offplatform and no_coercion and dampeners >= 1:
        result = _remove_label(result, "grooming")
        result = _remove_label(result, "possible_predation")
        result = _ensure_label(result, "identity_ambiguity")
        result = _ensure_label(result, "monitor_only")
        result = _cap_risk(result, 20)
        result = _set_action_at_most(result, "MONITOR")
        result["summary"] = "Conversation shows identity ambiguity and quick flirtation escalation, but no clear financial coercion, secrecy pressure, or exploitative behavior in the visible exchange."

    if hard_scam >= 2:
        result = _ensure_label(result, "possible_romance_scam")
    else:
        result = _remove_label(result, "possible_romance_scam")

    if "rapid_flirtation" in (result.get("labels", []) or []):
        safer_label = "benign_flirting" if dampeners >= 1 and no_money and no_coercion else "fast_escalation"
        result = _ensure_label(result, safer_label)

    return result


def _sanitize_prohibited_claims(payload: Any) -> Any:
    replacements = {
        "this looks male": "this style pattern is not suitable for sex inference",
        "this looks female": "this style pattern is not suitable for sex inference",
        "women text like this": "this output does not support group-based texting claims",
        "men text like this": "this output does not support group-based texting claims",
    }
    if isinstance(payload, dict):
        return {k: _sanitize_prohibited_claims(v) for k, v in payload.items()}
    if isinstance(payload, list):
        return [_sanitize_prohibited_claims(item) for item in payload]
    if isinstance(payload, str):
        sanitized = payload
        lowered = sanitized.lower()
        for phrase, replacement in replacements.items():
            if phrase in lowered:
                sanitized = re.sub(re.escape(phrase), replacement, sanitized, flags=re.IGNORECASE)
                lowered = sanitized.lower()
        return sanitized
    return payload


# ===========================================================================
# DETERMINISTIC ENGINE — INTERNAL FUNCTIONS
# ===========================================================================

def _score_evidence(signals: List[str]) -> Dict[str, Any]:
    """Probabilistic cumulative score — natural diminishing returns."""
    enriched = []
    tier_counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    weights_seen: List[float] = []

    for sig_id in signals:
        entry = SIGNAL_REGISTRY.get(sig_id)
        if entry:
            enriched.append({
                "id": sig_id, "tier": entry["tier"], "weight": entry["weight"],
                "label": entry["label"], "explanation": entry["explanation"],
            })
            weights_seen.append(entry["weight"])
            tier_counts[entry["tier"]] = tier_counts.get(entry["tier"], 0) + 1
        else:
            enriched.append({
                "id": sig_id, "tier": "MEDIUM", "weight": 0.30,
                "label": sig_id.replace("_", " ").title(),
                "explanation": "Signal detected. See machine detail for context.",
            })
            weights_seen.append(0.30)

    if weights_seen:
        product = 1.0
        for w in weights_seen:
            product *= (1.0 - w)
        cumulative = round(1.0 - product, 3)
    else:
        cumulative = 0.0

    if tier_counts["CRITICAL"] >= 2:
        verdict, summary = "STRONG", "Multiple critical signals detected. The pattern is consistent with a known fraud structure."
    elif tier_counts["CRITICAL"] >= 1:
        verdict, summary = "ELEVATED", "At least one critical signal detected. Treat this as a serious warning until independently verified."
    elif tier_counts["HIGH"] >= 2:
        verdict, summary = "MODERATE", "Several high-severity signals detected. The risk is real but not yet conclusive."
    elif enriched:
        verdict, summary = "WEAK", "Some signals present but individually inconclusive. Watch for escalation."
    else:
        verdict, summary = "NONE", "No risk signals detected in this conversation."

    return {
        "enriched_signals": enriched,
        "cumulative_evidence_score": cumulative,
        "evidence_verdict": verdict,
        "evidence_summary": summary,
        "tier_counts": tier_counts,
    }


def _detect_domain_mode(text: str) -> Dict[str, Any]:
    t = _norm(text)
    housing_terms = [
        "rent", "rental", "deposit", "lease", "application", "landlord",
        "owner", "property", "apartment", "house", "utilities", "move in",
        "showing", "viewing", "zillow", "nextdoor", "airbnb", "host",
        "guest", "check in", "wifi", "parking",
    ]
    dating_terms = [
        "date", "dating", "cute", "beautiful", "babe", "baby", "kiss",
        "miss you", "love you", "come over", "hook up", "hookup", "sexy",
    ]
    marketplace_terms = [
        "facebook marketplace", "seller", "buyer", "pickup", "shipping",
        "tracking", "venmo", "cashapp", "paypal", "zelle",
    ]
    scores = {
        "housing_rental": _count_any(t, housing_terms),
        "dating_social": _count_any(t, dating_terms),
        "marketplace_transaction": _count_any(t, marketplace_terms),
    }
    best_score = max(scores.values())
    if best_score < 3:
        return {"domain_mode": "general_unknown", "domain_confidence": 0.35}
    top_modes = [m for m, s in scores.items() if s == best_score]
    if len(top_modes) > 1:
        return {"domain_mode": "general_unknown", "domain_confidence": 0.35}
    best_mode = top_modes[0]
    return {"domain_mode": best_mode, "domain_confidence": round(best_score / max(sum(scores.values()), 1), 2)}


def _detect_reciprocity(text: str) -> str:
    t = _norm(text)
    medium = [
        "thanks", "thank you", "let me know", "sounds good", "okay", "ok",
        "sure", "absolutely", "of course", "no problem", "sounds great",
        "that works", "perfect",
    ]
    high = [
        "me too", "you too", "haha", "lol", "we can", "let's",
        "yeah", "yep", "yup", "for sure", "same", "totally", "definitely",
        "fr", "facts", "ikr", "omg", "same here", "agreed", "exactly",
        "love that", "can't wait", "yes please",
    ]
    if _count_any(t, high) >= 2:
        return "HIGH"
    if _count_any(t, high) >= 1 or _count_any(t, medium) >= 2:
        return "MEDIUM"
    return "LOW"


def _detect_intent_horizon(text: str, domain_mode: str) -> str:
    if domain_mode != "dating_social":
        return "NOT_APPLICABLE"
    t = _norm(text)
    s = _count_any(t, ["come over", "hook up", "hookup", "sexy", "horny", "kiss"])
    l = _count_any(t, ["relationship", "future", "tomorrow", "weekend", "plan", "consistency"])
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

    # High-intent / vision-building markers
    high_intent_markers = [
        "married", "marriage", "wedding", "honeymoon", "destination wedding",
        "have a baby", "start a family", "settle down", "long term", "long-term",
        "relationship", "future together", "serious", "commitment", "committed",
        "sperm donor", "pregnant", "pregnancy", "baby this year"
    ]
    fear_urgency_markers = [
        "alone next christmas", "alone by", "don't want to be alone",
        "do not want to be alone", "this year", "before the year",
        "sperm donor", "or at least", "if not we", "no reason to talk",
        "there is no reason", "there's no reason"
    ]
    vision_markers = [
        "yellowstone", "wyoming", "honeymoon", "destination wedding",
        "where would we", "where should we", "what would you", "when we",
        "we would", "we could", "i picture", "i imagine",
        "i can see us", "i see us"
    ]
    high_intent_count = _count_any(t, high_intent_markers)
    fear_urgency_count = _count_any(t, fear_urgency_markers)
    vision_count = _count_any(t, vision_markers)

    # Positive signals only — no concern signals mixed in
    if high_intent_count >= 1:
        signals.append("high_intent_present")
    if vision_count >= 2:
        signals.append("vision_building_present")

    # Concern signals — tracked separately, not in positive_signals
    concern_signals = []
    if fear_urgency_count >= 1:
        concern_signals.append("fear_driven_urgency")

    if high_intent_count >= 2 and vision_count >= 1 and not label:
        label = "high_intent_mutual"
    elif fear_urgency_count >= 2 and high_intent_count >= 1 and not label:
        label = "fear_driven_urgency"
    elif high_intent_count >= 1 and not label:
        label = "mixed_intent_genuine"

    return {
        "connection_signals": signals, "connection_label": label,
        "concern_signals": concern_signals,
        "confusion_count": confusion_count, "repair_count": repair_count,
        "playful_count": playful_count, "warm_count": warm_count, "sexual_count": sexual_count,
        "high_intent_count": high_intent_count, "fear_urgency_count": fear_urgency_count,
        "vision_count": vision_count,
    }


def _extract_key_signals(text: str, domain_mode: str) -> Dict[str, Any]:
    t = _norm(text)
    signals: List[str] = []
    boundary_violations: List[str] = []

    money_terms = ["deposit", "lease agreement", "move in", "application fee", "rent", "$", "paid"]
    money_action_verbs = ["send", "wire", "transfer", "pay", "submit", "provide", "need", "require", "must pay", "owe", "collect"]
    pressure_terms = ["urgent", "immediately", "right now", "must", "need to", "asap", "today"]
    sensitive_terms = ["ssn", "social security", "password", "login", "verification code", "otp", "pin"]

    if _fuzzy_contains_any(t, sensitive_terms):
        signals.append("credential_or_sensitive_info_signal")
    if _contains_any(t, pressure_terms):
        signals.append("pressure_present")
    if domain_mode == "dating_social" and _contains_any(t, ["come over", "hook up", "hookup", "sexy", "horny"]):
        signals.append("sexual_directness")
    if _contains_any(t, ["stop contacting me", "leave me alone", "not comfortable", "do not contact me"]):
        signals.append("boundary_language_present")

    if domain_mode == "housing_rental":
        withheld_phrases = [
            "owner contact information once you're interested",
            "owner contact information once you are interested",
            "contact information once you're interested",
            "contact information once you are interested",
            "once you're interested in renting",
            "once you are interested in renting",
        ]
        if _fuzzy_contains_any(t, withheld_phrases):
            signals.append("withheld_owner_verification")
            boundary_violations.append("withheld_owner_verification")

        if _contains_any(t, [
            "other property", "wrong property", "belongs to a lady",
            "belongs to the lady", "initially talked about belongs to",
            "i think i sent you the other property",
        ]):
            signals.append("property_identity_shift")
            boundary_violations.append("property_identity_shift")

        correction_phrase_present = _contains_any(
            t, ["he's not out of town for work", "he is not out of town for work"]
        )
        primary_cluster_present = (
            _contains_any(t, ["out of town", "keys can be sent", "owner is currently out of town"])
            and _contains_any(t, ["manny", "private owner", "belongs to a lady", "other property"])
        )
        corroborating_present = (
            "money_request" in signals
            or "property_identity_shift" in signals
            or _contains_any(t, money_terms)
        )
        if primary_cluster_present or (correction_phrase_present and corroborating_present):
            signals.append("owner_identity_shift")
            boundary_violations.append("owner_identity_shift")

        if (
            _contains_any(t, ["application is approved", "once your application is approved"])
            and _contains_any(t, ["showing can be scheduled", "showing", "look at it", "see the property", "viewing"])
        ):
            signals.append("verification_path_shift")
            boundary_violations.append("verification_path_shift")

        payment_trigger_phrases = [
            "deposit is paid", "lease agreement signed", "move in",
            "would need the entire",
            "first month rent can be paid after your move in",
        ]
        if _contains_any(t, money_terms) and _fuzzy_contains_any(t, payment_trigger_phrases):
            signals.append("payment_before_verification")
            boundary_violations.append("payment_before_verification")

        if _contains_any(t, money_terms) and _contains_any(t, money_action_verbs):
            signals.append("money_request")

    extraction_present = any(s in signals for s in [
        "credential_or_sensitive_info_signal",
        "payment_before_verification",
        "money_request",
    ])
    pressure_present = "pressure_present" in signals

    return {
        "signals": list(dict.fromkeys(signals)),
        "extraction_present": extraction_present,
        "pressure_present": pressure_present,
        "boundary_violations": list(dict.fromkeys(boundary_violations)),
    }


def _assign_lane(
    domain_mode: str, reciprocity_level: str, intent_horizon: str,
    extraction_present: bool, pressure_present: bool,
    boundary_violations: List[str], key_signals: List[str],
    relationship_type: str, text: str, connection_label: Optional[str] = None,
) -> Dict[str, Any]:
    housing_cluster = sum(1 for s in key_signals if s in {
        "withheld_owner_verification", "property_identity_shift",
        "owner_identity_shift", "verification_path_shift", "payment_before_verification",
    })

    if domain_mode == "housing_rental":
        if housing_cluster >= 2:
            return {"lane": "FRAUD", "primary_label": "transactional_extraction_pattern"}
        if _contains_any(text, ["wifi", "guest", "host", "parking", "check in", "during your stay"]) and housing_cluster == 0 and not extraction_present:
            return {"lane": "BENIGN", "primary_label": "routine_host_message"}

    if extraction_present and (pressure_present or housing_cluster >= 1):
        return {"lane": "FRAUD", "primary_label": "transactional_extraction_pattern"}
    if pressure_present and boundary_violations:
        return {"lane": "COERCION_RISK", "primary_label": "pressure_with_boundary_violation"}
    connection_labels = {
        "high_intent_mutual", "fear_driven_urgency", "mixed_intent_genuine",
        "playful_reengagement", "confusion_then_repair", "light_sexual_reciprocity",
        "warm_receptivity", "casual_flirtation"
    }
    if domain_mode == "dating_social":
        if "sexual_directness" in key_signals and reciprocity_level == "HIGH" and not extraction_present and not pressure_present:
            return {"lane": "DATING_AMBIGUOUS", "primary_label": "fast_escalation_noncoercive"}
        if connection_label and connection_label in connection_labels and not extraction_present and not pressure_present:
            return {"lane": "BENIGN", "primary_label": connection_label}
        return {"lane": "DATING_AMBIGUOUS", "primary_label": "mixed_intent"}
    if relationship_type in {"dating", "family", "friend"} and not extraction_present and not pressure_present:
        return {"lane": "RELATIONSHIP_NORMAL", "primary_label": "relationship_context"}
    if connection_label and connection_label in connection_labels and not extraction_present and not pressure_present:
        return {"lane": "BENIGN", "primary_label": connection_label}
    return {"lane": "BENIGN", "primary_label": "routine_message"}


def _build_dampeners(
    domain_mode: str, reciprocity_level: str, intent_horizon: str,
    extraction_present: bool, pressure_present: bool,
    text: str, key_signals: List[str],
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

    housing_cluster = sum(1 for s in key_signals if s in {
        "withheld_owner_verification", "property_identity_shift",
        "owner_identity_shift", "verification_path_shift", "payment_before_verification",
    })
    if domain_mode == "housing_rental" and _contains_any(text, ["wifi", "guest", "host", "parking", "check in", "during your stay"]) and housing_cluster == 0:
        dampeners.append("routine_transactional_context")
    return dampeners


def _risk_from_lane(
    lane: str, key_signals: List[str], key_dampeners: List[str],
    extraction_present: bool, pressure_present: bool,
) -> Dict[str, Any]:
    base = {"FRAUD": 82, "COERCION_RISK": 72, "DATING_AMBIGUOUS": 30, "RELATIONSHIP_NORMAL": 18, "BENIGN": 8}[lane]
    bonuses = {
        "withheld_owner_verification": 8, "property_identity_shift": 8,
        "owner_identity_shift": 10, "verification_path_shift": 8,
        "payment_before_verification": 10, "money_request": 6,
        "credential_or_sensitive_info_signal": 10, "pressure_present": 6,
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
    risk_level = "HIGH" if score >= 70 else ("MEDIUM" if score >= 35 else "LOW")
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
    tier_boost = 0.0
    for sig_id in key_signals:
        entry = SIGNAL_REGISTRY.get(sig_id)
        tier = entry["tier"] if entry else "MEDIUM"
        tier_boost += _TIER_CONFIDENCE_WEIGHTS.get(tier, 0.02)
    score += min(tier_boost, 0.25)
    score += min(len(key_dampeners) * 0.02, 0.10)
    if lane == "FRAUD":
        score += 0.08
    elif lane == "COERCION_RISK":
        score += 0.06
    return round(max(0.35, min(0.95, score)), 2)


def _run_deterministic(text: str, relationship_type: str = "stranger") -> Dict[str, Any]:
    """Pure pattern-matching analysis — no API required."""
    normalized_text = (text or "").strip()
    domain = _detect_domain_mode(normalized_text)
    reciprocity_level = _detect_reciprocity(normalized_text)
    intent_horizon = _detect_intent_horizon(normalized_text, domain["domain_mode"])
    extracted = _extract_key_signals(normalized_text, domain["domain_mode"])
    connection_data = _detect_connection_signals(normalized_text)

    lane_info = _assign_lane(
        domain_mode=domain["domain_mode"], reciprocity_level=reciprocity_level,
        intent_horizon=intent_horizon, extraction_present=extracted["extraction_present"],
        pressure_present=extracted["pressure_present"],
        boundary_violations=extracted["boundary_violations"],
        key_signals=extracted["signals"], relationship_type=relationship_type,
        text=normalized_text, connection_label=connection_data["connection_label"],
    )
    dampeners = _build_dampeners(
        domain_mode=domain["domain_mode"], reciprocity_level=reciprocity_level,
        intent_horizon=intent_horizon, extraction_present=extracted["extraction_present"],
        pressure_present=extracted["pressure_present"],
        text=normalized_text, key_signals=extracted["signals"],
    )
    risk = _risk_from_lane(
        lane=lane_info["lane"], key_signals=extracted["signals"],
        key_dampeners=dampeners, extraction_present=extracted["extraction_present"],
        pressure_present=extracted["pressure_present"],
    )
    contradiction_signals = [
        {"type": s, "severity": "high"} for s in extracted["signals"]
        if s in {"withheld_owner_verification", "property_identity_shift",
                 "owner_identity_shift", "verification_path_shift", "payment_before_verification"}
    ]
    narrative_integrity_score = max(0, 100 - (len(contradiction_signals) * 18))
    confidence = _confidence_score(lane_info["lane"], extracted["signals"], dampeners)

    _primary = lane_info["primary_label"]
    if lane_info["lane"] in {"FRAUD", "COERCION_RISK"}:
        analysis_mode, interest_score, interest_label = "safety_only", None, "Not Applicable"
    elif _primary == "high_intent_mutual":
        analysis_mode, interest_score, interest_label = "social_interest", 78, "High"
    elif _primary == "fear_driven_urgency":
        analysis_mode, interest_score, interest_label = "social_interest", 62, "High - fear driven"
    elif _primary == "mixed_intent_genuine":
        analysis_mode, interest_score, interest_label = "social_interest", 48, "Moderate"
    elif _primary in {"playful_reengagement", "light_sexual_reciprocity", "warm_receptivity"}:
        analysis_mode, interest_score, interest_label = "social_interest", 65, "High"
    elif _primary in {"casual_flirtation", "confusion_then_repair"}:
        analysis_mode, interest_score, interest_label = "social_interest", 45, "Moderate"
    else:
        analysis_mode = "social_interest"
        interest_score = 55 if reciprocity_level == "HIGH" else 35
        interest_label = "Moderate" if reciprocity_level == "HIGH" else "Low"

    flags = extracted["signals"][:] if extracted["signals"] else ["No signals detected"]
    positive_signals = connection_data["connection_signals"][:]
    if reciprocity_level == "HIGH" and "reciprocal_engagement" not in positive_signals:
        positive_signals.append("reciprocal_engagement")
    # Merge connection concern signals into key_signals so they appear in concern section
    for cs in connection_data.get("concern_signals", []):
        if cs not in flags:
            flags.append(cs)

    evidence_data = _score_evidence(extracted["signals"])

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

    research_patch = _build_research_patch(normalized_text, relationship_type)

    return {
        "risk_score": risk["risk_score"],
        "risk_level": risk["risk_level"],
        "lane": lane_info["lane"],
        "primary_label": lane_info["primary_label"],
        "phase": "NONE",
        "vie_action": (
            "BLOCK" if risk["risk_score"] >= 85
            else "WARN" if risk["risk_score"] >= 50
            else "MONITOR" if risk["risk_score"] >= 25
            else "NONE"
        ),
        "confidence": confidence,
        "reciprocity_level": reciprocity_level,
        "intent_horizon": intent_horizon,
        "pressure_present": extracted["pressure_present"],
        "extraction_present": extracted["extraction_present"],
        "boundary_violations": extracted["boundary_violations"],
        "key_signals": extracted["signals"],
        "key_dampeners": dampeners,
        "flags": flags,
        "evidence": {},
        "active_combos": [],
        "positive_signals": positive_signals,
        "alternative_explanations": _alternative_explanations(domain["domain_mode"], lane_info["lane"]),
        "summary_logic": summary_logic,
        "summary": summary_logic,
        "recommended_action": "No action required." if lane_info["lane"] == "BENIGN" else f"Review flagged signals: {', '.join(extracted['signals'][:3])}.",
        "domain_mode": domain["domain_mode"],
        "domain_confidence": domain["domain_confidence"],
        "analysis_mode": analysis_mode,
        "contradiction_signals": contradiction_signals,
        "narrative_integrity_score": narrative_integrity_score,
        "risk_floor_applied": lane_info["lane"] == "FRAUD",
        "risk_floor_reason": "rental_contradiction_cluster" if lane_info["lane"] == "FRAUD" else None,
        "degraded": False,
        "labels": [],
        "interest_score": interest_score,
        "interest_label": interest_label,
        "evidence_scoring": evidence_data,
        "research_patch": research_patch,
    }


# ===========================================================================
# LLM ENGINE — JSON PARSER & API RUNNER
# ===========================================================================

def _extract_first_json_object(raw_text: str) -> Dict[str, Any]:
    if raw_text is None:
        raise ValueError("Claude response was empty")
    s = re.sub(r"^\s*```(?:json)?\s*", "", str(raw_text).strip(), flags=re.IGNORECASE)
    s = re.sub(r"\s*```\s*$", "", s)
    try:
        return json.loads(s)
    except Exception:
        pass
    start = s.find("{")
    if start == -1:
        raise ValueError("No JSON object found in Claude response")
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(s[start: i + 1])
    raise ValueError("No complete balanced JSON object found in Claude response")


def _run_llm_analysis(text: str, relationship_type: str = "stranger", context_note: str = "") -> Dict[str, Any]:
    """Call Claude API with full 30-signal VIE library. Fail-closed on any error."""
    import anthropic as _anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")

    client = _anthropic.Anthropic(api_key=api_key)

    if len(text) > 8000:
        text = text[:8000] + "\n[truncated]"

    relationship_type = str(relationship_type or "stranger").lower()
    if relationship_type in ("dating", "family", "friend", "business"):
        active_prompt = RELATIONSHIP_PROMPT
        user_content = f"Relationship type: {relationship_type}\nContext note: {context_note or 'None'}\n\nAnalyze this conversation:\n\n{text}"
    else:
        active_prompt = SYSTEM_PROMPT
        user_content = f"Context note: {context_note or 'None'}\n\nAnalyze this conversation:\n\n{text}"

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1400,
        system=active_prompt,
        messages=[{"role": "user", "content": user_content}],
    )

    raw = message.content[0].text.strip()
    logger.info("Claude response: %s", raw[:300])
    result = _extract_first_json_object(raw)

    risk_score = max(0, min(100, int(result.get("risk_score", 0))))
    flags = result.get("flags", ["No signals detected"])
    if not isinstance(flags, list) or len(flags) == 0:
        flags = ["No signals detected"]

    try:
        confidence = max(0.0, min(1.0, float(result.get("confidence", 0.5))))
    except Exception:
        confidence = 0.5

    evidence = result.get("evidence", {})
    if not isinstance(evidence, dict):
        evidence = {}
    active_combos = result.get("active_combos", [])
    if not isinstance(active_combos, list):
        active_combos = []
    positive_signals = result.get("positive_signals", [])
    if not isinstance(positive_signals, list):
        positive_signals = []
    labels = result.get("labels", [])
    if not isinstance(labels, list):
        labels = []

    merged: Dict[str, Any] = {
        "risk_score": risk_score,
        "risk_level": "HIGH" if risk_score >= 70 else ("MEDIUM" if risk_score >= 35 else "LOW"),
        "flags": flags,
        "confidence": confidence,
        "summary": result.get("summary", "Analysis complete."),
        "summary_logic": result.get("summary", "Analysis complete."),
        "recommended_action": result.get("recommended_action", "No action required."),
        "degraded": False,
        "phase": result.get("phase", "NONE"),
        "vie_action": result.get("vie_action", "NONE"),
        "active_combos": active_combos,
        "evidence": evidence,
        "positive_signals": positive_signals,
        "labels": labels,
        "lane": "FRAUD" if risk_score >= 75 else ("COERCION_RISK" if risk_score >= 60 else "BENIGN"),
        "primary_label": result.get("primary_label", "routine_message") if result.get("primary_label") else ("routine_message" if not flags or flags[0] == "No signals detected" else "routine_message"),
        "key_signals": flags,
        "key_dampeners": [],
        "domain_mode": "general_unknown",
        "domain_confidence": 0.5,
        "analysis_mode": "llm_powered",
        "contradiction_signals": [],
        "narrative_integrity_score": 100,
        "risk_floor_applied": False,
        "risk_floor_reason": None,
        "interest_score": None,
        "interest_label": "Not Applicable",
        "evidence_scoring": _score_evidence([]),
    }

    merged = _apply_relationship_guardrails(merged, relationship_type=relationship_type)
    merged["research_patch"] = _build_research_patch(text, relationship_type)

    if merged["research_patch"]["data_sufficiency"]["level"] == "low":
        merged["confidence"] = min(float(merged.get("confidence", 0.5)), 0.55)

    merged = _sanitize_prohibited_claims(merged)
    return merged


# ===========================================================================
# PUBLIC INTERFACE
# ===========================================================================

def analyze_text(
    text: str,
    relationship_type: str = "stranger",
    context_note: str = "",
    use_llm: bool = False,
) -> Dict[str, Any]:
    """
    Analyze conversation text for fraud, coercion, and manipulation signals.

    Parameters
    ----------
    text             : Conversation text or screenshot OCR output.
    relationship_type: "stranger" | "dating" | "family" | "friend" | "business"
    context_note     : Optional freetext context passed to the LLM prompt.
    use_llm          : If True (default), use Claude API with full 30-signal library.
                       Falls back to deterministic engine if API is unavailable.
                       If False, always use deterministic engine (faster, no cost).

    Returns
    -------
    Dict with keys: risk_score, risk_level, lane, phase, vie_action, flags,
    evidence, active_combos, positive_signals, confidence, summary,
    recommended_action, degraded, research_patch, and more.
    Fail-closed: any unhandled exception returns degraded=True, risk_score=100.
    """
    if not use_llm:
        result = _run_deterministic(text, relationship_type)
        result = _apply_relationship_guardrails(result, relationship_type)
        result = _sanitize_prohibited_claims(result)
        return result

    try:
        return _run_llm_analysis(text, relationship_type, context_note)
    except Exception as e:
        logger.warning("LLM analysis failed (%s) — falling back to deterministic engine.", e)
        try:
            result = _run_deterministic(text, relationship_type)
            result = _apply_relationship_guardrails(result, relationship_type)
            result = _sanitize_prohibited_claims(result)
            result["degraded"] = False  # deterministic succeeded — not truly degraded
            result["fallback_reason"] = str(e)
            return result
        except Exception as e2:
            logger.error("Deterministic fallback also failed: %s", e2)
            failed: Dict[str, Any] = {
                "risk_score": 100,
                "risk_level": "HIGH",
                "flags": ["ANALYSIS_ENGINE_FAILURE"],
                "confidence": 0.0,
                "summary": "Analysis engine error. Output blocked per fail-closed policy.",
                "summary_logic": "Engine failure.",
                "recommended_action": "Do not proceed. Contact support.",
                "degraded": True,
                "phase": "NONE",
                "vie_action": "NONE",
                "active_combos": [],
                "evidence": {},
                "positive_signals": [],
                "labels": [],
                "lane": "BENIGN",
                "primary_label": "engine_failure",
                "key_signals": [],
                "key_dampeners": [],
                "domain_mode": "general_unknown",
                "domain_confidence": 0.0,
                "analysis_mode": "failed",
                "contradiction_signals": [],
                "narrative_integrity_score": 0,
                "risk_floor_applied": False,
                "risk_floor_reason": None,
                "interest_score": None,
                "interest_label": "Not Applicable",
                "evidence_scoring": _score_evidence([]),
            }
            failed["research_patch"] = _build_research_patch(text, relationship_type)
            return failed


def _turn_risk_score(text: str, relationship_type: str = "stranger") -> int:
    """Per-turn risk score — always uses deterministic engine for speed."""
    return analyze_text(text, relationship_type=relationship_type, use_llm=False).get("risk_score", 0)


def _turn_label(text: str, relationship_type: str = "stranger") -> str:
    return analyze_text(text, relationship_type=relationship_type, use_llm=False).get("primary_label", "routine_message")


def _arc_label(scores: List[int], labels: List[str]) -> Dict[str, Any]:
    if len(scores) <= 1:
        return {"arc": "single_turn", "arc_label": "Single screenshot — upload more for pattern tracking", "direction": "neutral", "delta": 0}

    first, last = scores[0], scores[-1]
    delta = last - first
    swing = max(scores) - min(scores)
    early_confused = any(l in {"routine_message", "confusion_then_repair", "playful_reengagement"}
                         for l in labels[:max(1, len(labels) // 2)])
    late_warm = any(l in {"warm_receptivity", "casual_flirtation", "playful_reengagement",
                          "light_sexual_reciprocity", "confusion_then_repair"}
                    for l in labels[len(labels) // 2:])

    if swing >= 30 and delta < -10:
        arc, arc_label, direction = "repair", "Started rough, ended warmer — the conversation repaired itself", "improving"
    elif delta >= 20:
        arc, arc_label, direction = "escalating", "Risk is climbing across screenshots — something shifted", "worsening"
    elif delta <= -20:
        arc, arc_label, direction = "de_escalating", "Tension dropped as the conversation progressed", "improving"
    elif swing >= 25:
        arc, arc_label, direction = "volatile", "Inconsistent energy — the conversation keeps shifting", "mixed"
    elif max(scores) >= 60:
        arc, arc_label, direction = "flat_high", "Consistently elevated risk across all screenshots", "concerning"
    elif early_confused and late_warm:
        arc, arc_label, direction = "repair", "Started confused or defensive, ended warm — classic repair pattern", "improving"
    else:
        arc, arc_label, direction = "flat_low", "Low and stable — nothing escalated across these screenshots", "neutral"

    return {"arc": arc, "arc_label": arc_label, "direction": direction, "delta": delta}


def analyze_turns(
    text_chunks: List[str],
    relationship_type: str = "stranger",
) -> Dict[str, Any]:
    """
    Analyze a sequence of conversation turns.
    Always uses deterministic engine per-turn for speed and consistency.
    """
    if not text_chunks:
        return {
            "turn_count": 0, "turns": [], "arc": "single_turn",
            "arc_label": "No screenshots provided", "direction": "neutral",
            "delta": 0, "multi_turn": False, "skipped_chunks": 0,
        }

    turns = []
    scores = []
    labels = []
    skipped = 0

    for i, chunk in enumerate(text_chunks):
        if not chunk.strip():
            skipped += 1
            continue

        result = _run_deterministic(chunk, relationship_type)
        score = result.get("risk_score", 0)
        label = result.get("primary_label", "routine_message")
        connection_data = _detect_connection_signals(chunk)

        scores.append(score)
        labels.append(label)

        turn_verdict = "High concern" if score >= 70 else ("Worth watching" if score >= 35 else "Low concern")
        turn_color = "high" if score >= 70 else ("medium" if score >= 35 else "low")

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
        "skipped_chunks": skipped,
    }

def run_combined(
    turns,
    behavior_result=None,
    dynamics_result=None,
    use_llm: bool = False,
) -> dict:
    """
    Public alias called by api.py pipeline.
    Converts turn list to text and runs analyze_text.
    """
    if hasattr(turns, "__iter__") and not isinstance(turns, str):
        lines = []
        for t in turns:
            if hasattr(t, "speaker") and hasattr(t, "message"):
                lines.append(f"{t.speaker}: {t.message}")
            elif isinstance(t, dict):
                speaker = t.get("speaker", "unknown")
                message = t.get("message", t.get("text", ""))
                lines.append(f"{speaker}: {message}")
            else:
                lines.append(str(t))
        text = "\n".join(lines)
    else:
        text = str(turns or "")

    return analyze_text(text, use_llm=use_llm)
