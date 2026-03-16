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
