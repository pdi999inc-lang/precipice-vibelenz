"""
analyzer.py - VibeLenz LLM analyzer powered by the full 30-signal VIE behavioral library.

Signals: 13 CRITICAL | 9 HIGH | 4 MEDIUM | 4 LOW
Includes: Phase detection, combo amplification, VIE action mapping
Fail-closed: any API failure returns degraded=True, risk_score=100.
"""

import json
import logging
import os
from typing import Any, Dict

import anthropic

logger = logging.getLogger("vibelenz.analyzer")

SYSTEM_PROMPT = """You are VibeLenz, a safety analysis engine powered by the Verified Interaction Engine (VIE).

You analyze conversation text extracted from screenshots using the full VIE 30-signal behavioral library.

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
  "positive_signals": ["reciprocal_engagement", "no_financial_topics"],  // list ALL observed trust signals here, never leave empty if trust signals present
  "confidence": <float 0.0-1.0>,
  "summary": "<plain language risk summary including phase and pattern identification>",
  "recommended_action": "<specific action — if LAW_ENFORCEMENT_REFERRAL, state this explicitly>",
  "degraded": false
}

If no risk signals detected return risk_score 0, phase NONE, vie_action NONE, flags ["No signals detected"], evidence {}. However always populate positive_signals with any trust indicators you observed even when risk is 0. A score of 0 with positive signals is the ideal result for a healthy conversation."""


RELATIONSHIP_PROMPT = """You are VibeLenz, a communication safety and dynamics analyzer.

You analyze conversation text to identify harmful communication patterns in ongoing personal relationships.
This is NOT a fraud/scam analysis. This is a behavioral dynamics analysis.

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

def _norm_signal_name(x):
    return str(x).strip().lower().replace(" ", "_")

def _extract_signal_names(result):
    names = set()

    for item in result.get("flags", []) or []:
        if isinstance(item, str):
            names.add(_norm_signal_name(item))
        elif isinstance(item, dict):
            for key in ("signal", "name", "id", "label"):
                if item.get(key):
                    names.add(_norm_signal_name(item[key]))

    for item in result.get("active_combos", []) or []:
        if isinstance(item, str):
            names.add(_norm_signal_name(item))
        elif isinstance(item, dict):
            for key in ("signal", "name", "id", "label"):
                if item.get(key):
                    names.add(_norm_signal_name(item[key]))

    for item in result.get("positive_signals", []) or []:
        if isinstance(item, str):
            names.add(_norm_signal_name(item))
        elif isinstance(item, dict):
            for key in ("signal", "name", "id", "label"):
                if item.get(key):
                    names.add(_norm_signal_name(item[key]))

    return names

def _set_summary(result, text):
    result["summary"] = text
    result = _apply_relationship_guardrails(result, relationship_type=relationship_type)
    return result

def _ensure_label(result, label):
    labels = result.get("labels", []) or []
    if label not in labels:
        labels.append(label)
    result["labels"] = labels
    return result

def _remove_label(result, label):
    labels = [x for x in (result.get("labels", []) or []) if str(x).lower() != label.lower()]
    result["labels"] = labels
    return result

def _cap_risk(result, cap):
    try:
        score = int(result.get("risk_score", 0))
    except Exception:
        score = 0
    result["risk_score"] = min(score, cap)
    return result

def _set_action_at_most(result, max_action="MONITOR"):
    current = str(result.get("vie_action", "NONE")).upper()
    order = {"NONE": 0, "SOFT_FLAG": 1, "MONITOR": 2, "WARN": 3, "BLOCK": 4, "LAW_ENFORCEMENT_REFERRAL": 5}
    if order.get(current, 0) > order.get(max_action, 2):
        result["vie_action"] = max_action
    return result

def _apply_relationship_guardrails(result, relationship_type="stranger"):
    relationship_type = str(relationship_type or "stranger").lower()
    if relationship_type == "stranger":
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

    # Block grooming unless several hard predation conditions are present
    if hard_groom < 3:
        result = _remove_label(result, "grooming")
        result = _remove_label(result, "early stage grooming indicators")
        result = _remove_label(result, "predation")
        if result.get("phase") in {"GROOMING", "COERCION", "ENDGAME"}:
            result["phase"] = "NONE"

    # If there is quick sexual/flirty escalation without hard exploitation, downgrade it
    if low_rapid >= 1 and hard_groom == 0 and hard_scam == 0:
        result = _ensure_label(result, "rapid_flirtation")
        result = _ensure_label(result, "needs_verification")
        result = _cap_risk(result, 24)
        result = _set_action_at_most(result, "MONITOR")
        result = _set_summary(
            result,
            "Conversation shows identity ambiguity and quick flirtation escalation, but no clear financial coercion or exploitative behavior in the visible exchange."
        )

    # Explicit false-positive guardrail
    if no_money and no_offplatform and no_coercion and dampeners >= 1:
        result = _remove_label(result, "grooming")
        result = _remove_label(result, "possible_predation")
        result = _ensure_label(result, "identity_ambiguity")
        result = _ensure_label(result, "monitor_only")
        result = _cap_risk(result, 20)
        result = _set_action_at_most(result, "MONITOR")
        result = _set_summary(
            result,
            "Conversation shows identity ambiguity and quick flirtation escalation, but no clear financial coercion, secrecy pressure, or exploitative behavior in the visible exchange."
        )

    # Narrow romance scam label
    if hard_scam >= 2:
        result = _ensure_label(result, "possible_romance_scam")
    else:
        result = _remove_label(result, "possible_romance_scam")

    # Prefer safer narrow labels
    if "rapid_flirtation" in (result.get("labels", []) or []):
        result = _ensure_label(result, "benign_flirting" if dampeners >= 1 and no_money and no_coercion else "fast_escalation")

    return result



def _extract_first_json_object(raw_text: str):
    import json
    import re

    if raw_text is None:
        raise ValueError("Claude response was empty")

    s = str(raw_text).strip()

    s = re.sub(r"^\s*```(?:json)?\s*", "", s, flags=re.IGNORECASE)
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
                    candidate = s[start:i + 1]
                    return json.loads(candidate)

    raise ValueError("No complete balanced JSON object found in Claude response")


def analyze_text(text: str, relationship_type: str = "stranger", context_note: str = "") -> Dict[str, Any]:
    """
    Analyze conversation text using Claude API.
    relationship_type: "stranger" | "dating" | "family" | "friend"
    Fail-closed: any exception returns degraded result with score=100.
    """
    try:
        return _run_analysis(text, relationship_type, context_note)
    except Exception as e:
        logger.error(f"Analysis failure: {e}")
        return {
            "risk_score": 100,
            "flags": ["ANALYSIS_ENGINE_FAILURE"],
            "confidence": 0.0,
            "summary": "Analysis engine error. Output blocked per fail-closed policy.",
            "recommended_action": "Do not proceed. Contact support.",
            "degraded": True,
        }


def _run_analysis(text: str, relationship_type: str = "stranger", context_note: str = "") -> Dict[str, Any]:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")

    client = anthropic.Anthropic(api_key=api_key)

    if len(text) > 8000:
        text = text[:8000] + "\n[truncated]"

    # Route to appropriate system prompt
    if relationship_type in ("dating", "family", "friend", "business"):
        active_prompt = RELATIONSHIP_PROMPT
        user_content = f"Relationship type: {relationship_type}\n\nAnalyze this conversation:\n\n{text}"
    else:
        active_prompt = SYSTEM_PROMPT
        user_content = f"Analyze this conversation:\n\n{text}"

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        system=active_prompt,
        messages=[
            {
                "role": "user",
                "content": user_content
            }
        ]
    )

    raw = message.content[0].text.strip()
    logger.info(f"Claude response: {raw[:300]}")

    if "```" in raw:
        raw = raw.replace("```json", "").replace("```", "").strip()

    result = json.loads(raw)

    risk_score = max(0, min(100, int(result.get("risk_score", 0))))
    flags = result.get("flags", ["No signals detected"])
    if not isinstance(flags, list) or len(flags) == 0:
        flags = ["No signals detected"]
    confidence = max(0.0, min(1.0, float(result.get("confidence", 0.5))))

    evidence = result.get("evidence", {})
    if not isinstance(evidence, dict):
        evidence = {}

    return {
        "risk_score": risk_score,
        "flags": flags,
        "confidence": confidence,
        "summary": result.get("summary", "Analysis complete."),
        "recommended_action": result.get("recommended_action", "No action required."),
        "degraded": False,
        "phase": result.get("phase", "NONE"),
        "vie_action": result.get("vie_action", "NONE"),
        "active_combos": result.get("active_combos", []),
        "evidence": evidence,
        "positive_signals": result.get("positive_signals", []),
    }
