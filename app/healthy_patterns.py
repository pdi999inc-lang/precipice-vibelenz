"""
app/healthy_patterns.py

VibeLenz healthy patterns — what's going RIGHT in a conversation, named as
specifically as the concerns are, with the line that shows it.

Copyright © 2026 Ricky Sessums. All rights reserved.

WHAT IT DOES
  Two families, all deterministic and quote-backed:

  MIRRORS (the healthy counterpart of each concern pattern)
    feelings_validated     <-> concern_minimized
    concrete_plans         <-> future_faking
    offers_to_meet         <-> verification_dodging
    owned_apology          <-> blame_inversion
    boundary_respected     <-> pressure / secrecy
    open_about_you         <-> secrecy_request
    asked_and_met          <-> user_rationalizing
  BROADER (relationship research: Gottman bids/fondness, Gable capitalization)
    curious_about_you, remembers_details, celebrates_you, appreciation

  It also upgrades the existing "What's working" chips: each key gets the
  glossary's friendly title and explanation instead of a raw key.

WHY DETERMINISTIC
  analyzer_combined.py deliberately discards most LLM positives ("LLM
  hallucinates positive signals"). Positives here only fire on an actual line.

SAFETY DECISIONS (deliberate; do not loosen without a written reason)
  - Never shown on FRAUD / COERCION_RISK lanes, risk_score >= 60, or when any
    money concern is present. Mirrors the analyzer's C6 gate: warmth inside a
    scam is often part of the scam.
  - Risk mode shows only VERIFIABLE counter-indicators (RISK_ELIGIBLE), in
    clinical voice, with a caveat that they do not offset the concerns.
    Love-bombing-compatible positives (curiosity, appreciation, celebrating)
    are never shown in risk mode.
  - Never says safe / all clear / trust them. Tests enforce it.
  - Direction matters for every pattern, so unlabeled or user_side="mix"
    text yields no patterns (chips still upgrade).
  - Additive only: never changes lane, risk_score, flags, or positive_signals.
  - Pure functions. No DB, network, clock, or randomness.

INTEGRATION (app/main.py) — after subtle_patterns, since it reads that result:
    from app.healthy_patterns import build_healthy_patterns
    payload["healthy_patterns"] = build_healthy_patterns(payload)   # inside try/except
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.subtle_patterns import (
    CONCRETE_TIME, DEFLECT_NEAR, YOU_CONCERN, _RX, _as_list, _eligible, _hits, _quote, parse_turns,
)

SCHEMA_VERSION = "healthy.v1"
CONNECTION = "connection"
RISK = "risk"

CLINICAL_WORDS = ("risk", "pressure", "danger", "flag", "signal", "elevated", "assessment")
# Positives must never over-promise, in either voice.
OVERPROMISE = ("safe", "all clear", "trust them", "nothing to worry", "guaranteed", "definitely likes")

BLOCKING_LANES = {"FRAUD", "COERCION_RISK"}
MONEY_CONCERNS = {"money_request", "payment_before_verification", "credential_or_sensitive_info_signal",
                  "financial_ask_escalation", "trust_calibration_small_ask", "lure_and_pivot"}

# Only these lower concern in a risk read: each is checkable in the real world.
RISK_ELIGIBLE = {"offers_to_meet", "concrete_plans", "open_about_you", "boundary_respected"}

ORDER = ["offers_to_meet", "concrete_plans", "feelings_validated", "owned_apology",
         "boundary_respected", "open_about_you", "asked_and_met",
         "celebrates_you", "remembers_details", "curious_about_you", "appreciation"]

FAMILY = {k: "mirror" for k in ORDER[:7]}
FAMILY.update({k: "broader" for k in ORDER[7:]})

# If a mirror concern fired too, say so plainly rather than hide either.
MIRROR_OF = {
    "feelings_validated": "concern_minimized", "concrete_plans": "future_faking",
    "offers_to_meet": "verification_dodging", "open_about_you": "secrecy_request",
    "asked_and_met": "user_rationalizing",
}

# Existing chip keys a detected concern contradicts: suppress the chip.
CHIP_SUPPRESSED_BY = {"vision_building_present": "future_faking", "high_intent_present": "future_faking"}

COPY: Dict[str, Dict[str, Dict[str, str]]] = {
    "feelings_validated": {
        CONNECTION: {"title": "They took your feelings seriously",
                     "body": "You raised something that bothered you, and instead of brushing it off they acknowledged it. That's the move that keeps small hurts from turning into big ones."}},
    "concrete_plans": {
        CONNECTION: {"title": "They make real plans",
                     "body": "Not just 'we should hang out sometime.' An actual day, an actual time. Specific plans are one of the clearest ways people show they mean it."},
        RISK: {"title": "Concrete scheduling",
               "body": "Proposes or confirms a specific date or time. Near-term commitments are verifiable and inconsistent with future-faking."}},
    "offers_to_meet": {
        CONNECTION: {"title": "They want to actually see you",
                     "body": "They offered a call or a meetup themselves. Someone who wants to be seen, in real life or on camera, is showing you who they are."},
        RISK: {"title": "Offers live verification",
               "body": "Counterparty proposes video contact or an in-person meeting. Voluntary verification is inconsistent with romance-fraud verification avoidance."}},
    "owned_apology": {
        CONNECTION: {"title": "They owned it",
                     "body": "A real apology names what they did, not how you took it. No 'sorry you feel that way,' no 'sorry, but.' That's rarer than it should be."}},
    "boundary_respected": {
        CONNECTION: {"title": "They respected your no",
                     "body": "You set a limit or slowed things down, and they were fine with it. How someone handles a small no tells you a lot about how they'll handle a big one."},
        RISK: {"title": "Boundary accepted",
               "body": "A stated limit was accepted without escalation or renegotiation. Inconsistent with coercive persistence."}},
    "open_about_you": {
        CONNECTION: {"title": "They're open about you",
                     "body": "They mention you to their people, or want to meet yours. Someone who brings you into their life isn't trying to keep you in a separate box."},
        RISK: {"title": "Third-party transparency",
               "body": "Counterparty references disclosing the relationship to friends or family, or invites contact with them. Inconsistent with isolation or secrecy tactics."}},
    "asked_and_met": {
        CONNECTION: {"title": "You asked, and they showed up",
                     "body": "You said plainly what you wanted, without apologizing for it, and they said yes. That's the whole thing working the way it should."}},
    "curious_about_you": {
        CONNECTION: {"title": "They're curious about you",
                     "body": "They keep asking about your life and your thoughts, not just talking about theirs. Curiosity is one of the steadiest signs of real interest."}},
    "remembers_details": {
        CONNECTION: {"title": "They remember what you tell them",
                     "body": "They followed up on something you mentioned before. Remembering the small stuff means they were actually listening."}},
    "celebrates_you": {
        CONNECTION: {"title": "They're happy for your wins",
                     "body": "You shared good news and they got into it with you. Research on couples finds how people respond to good news predicts closeness even more than how they handle bad news."}},
    "appreciation": {
        CONNECTION: {"title": "They say thank you",
                     "body": "They notice and name what you do for them. Small appreciation said out loud is one of the habits that keeps things warm long-term."}},
}

COEXIST_NOTE = "Both showed up here: {pos} and {neg}. Mixed is normal. Notice which one keeps happening."
RISK_CAVEAT = "Counter-indicators below are observed in the text. They do not offset the concerns above."
CHIP_MEANS_FALLBACK = ""

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------
VALIDATE = [
    _RX(r"\b(?:you're|you are|youre) (?:totally |completely |absolutely )?right\b"),
    _RX(r"\bthat (?:makes (?:total )?sense|is fair|'s fair|'s valid|is valid)\b"),
    _RX(r"\bi (?:hear you|get it|get why|understand why|can see why|totally get)\b"),
    _RX(r"\bi(?:'m| am) (?:really )?sorry (?:i|that i) (?:hurt|upset|made you|didn't)"),
    _RX(r"\byour feelings (?:are|make)\b|\bit makes sense (?:you|that you)\b"),
]

PLAN_VERB = _RX(r"\b(?:let's|lets|want to|wanna|how about|are you free|you free|can we|could we|shall we|would you like to|down to|i'll pick you up|see you|meet (?:you|me)|i booked|i made (?:a )?reservation)")

MEET_OFFER = [
    _RX(r"\b(?:let's|lets|want to|wanna|we should|can we|could we|how about|happy to|i'd love to|i would love to|down to) (?:\w+ ){0,2}(?:video ?call|facetime|face ?time|video ?chat|hop on (?:a )?call|meet (?:up|in person)?|grab (?:a )?(?:coffee|drink|food|dinner|lunch)|get (?:coffee|dinner|lunch|a drink|food)|hang out)"),
    _RX(r"\b(?:call|facetime) me (?:tonight|now|later|tomorrow)\b"),
]

OWNED = [
    _RX(r"\b(?:that's|that is|thats) (?:on me|my fault|my bad)\b"),
    _RX(r"\bi (?:was wrong|messed up|screwed up|dropped the ball|should have|shouldn't have)\b"),
    _RX(r"\bi(?:'m| am) (?:so |really )?sorry (?:for|i|that i|about) (?!you feel|if you)"),
    _RX(r"\bi apologize for\b"),
    _RX(r"\byou didn't deserve that\b|\bi'll do better\b|\bi won't do that again\b"),
]
# Non-owning apologies: conditional, deflecting, or non-specific ("whatever I did").
FAKE_APOLOGY = _RX(r"\bsorry (?:you feel|if you|you took|you're upset)|\bsorry,? but\b|\bsorry (?:not sorry)\b|\bsorry for (?:whatever|anything|everything) (?:i|that)\b")
# A plan taken back in the same breath is not a plan.
RETRACT = _RX(r"\b(?:never ?mind|nvm|actually wait|scratch that|forget it|i can't anymore|something came up)\b")

YOU_BOUNDARY = _RX(r"\b(?:i'm not ready|im not ready|not ready (?:for|to)|i'd rather not|i would rather not|can we slow (?:down|it down)|slow things down|i'm not comfortable|not comfortable (?:with|sending|doing)|i need (?:some )?space|no thanks|not tonight|i don't want to (?:send|share|do that))\b")
ACCEPT = [
    _RX(r"\b(?:no worries|no pressure|totally understand|completely understand|i understand|take your time|whenever you're ready|whenever youre ready|no rush|of course|that's (?:totally )?fine|thats fine|i respect that|all good|fair enough)\b"),
]
ESCALATE_AFTER_NO = _RX(r"\b(?:come on|just (?:this )?once|why not|you owe|if you really|don't be like that|stop being)\b")

OPEN = [
    _RX(r"\bi (?:told|was telling) (?:my )?(?:mom|mum|dad|mother|father|parents|family|friends|sister|brother|best friend|roommate|coworkers?) about you\b"),
    _RX(r"\b(?:want|like|love) (?:you|for you) to meet my (?:mom|mum|dad|parents|family|friends|sister|brother|best friend)"),
    _RX(r"\bi(?:'d| would) love to meet your (?:friends|family|mom|dad|sister|brother|best friend|people)\b"),
    _RX(r"\bbring (?:your|a) friends?\b|\bcome to (?:my|our) (?:\w+ )?(?:party|bbq|barbecue|game night|birthday|dinner)"),
]

YOU_ASK = _RX(r"\b(?:i need you to|it(?:'s| is) important to me|it matters to me|i'd like (?:us|you) to|i want us to|what i need is|can you please|could you please|i'd really like)\b")
AGREE = _RX(r"\b(?:yes|yeah|absolutely|of course|you got it|deal|done|i will|i can do that|for sure|definitely|100%)\b")

CURIOUS_Q = _RX(r"^(?:so |and |also |wait |ok |okay )?(?:what|how|why|where|who|which|when|do|did|are|have|would|could|can|tell me)\b[^?]{0,120}\?")
ABOUT_YOU = _RX(r"\b(?:you|your|you're|you've|yours)\b")

REMEMBER = [
    _RX(r"\bhow did (?:the|your) [\w' ]{1,30} go\b"),
    _RX(r"\bdid you end up\b"),
    _RX(r"\byou (?:mentioned|said|told me) (?:you|your|that you|last)\b"),
    _RX(r"\bhow(?:'s| is| was) your (?:mom|dad|sister|brother|dog|cat|interview|exam|test|presentation|trip|recital|game|first day|appointment|meeting)\b"),
]

YOU_GOOD_NEWS = _RX(r"\b(?:i got (?:the job|promoted|in|accepted|the apartment|a raise|an offer)|i passed|i finished|i graduated|i won|i did it|guess what|got the job|got promoted|good news)\b")
CELEBRATE = _RX(r"\b(?:congrats|congratulations|so proud|that's amazing|thats amazing|that's huge|thats huge|you earned (?:it|this)|tell me everything|i knew you (?:could|would)|let's celebrate|we have to celebrate|so happy for you)\b")

APPRECIATE = _RX(r"\b(?:thank you for|thanks for (?:\w+ing|being|the|always|listening|checking)|i appreciate (?:you|that|it)|means a lot|that was (?:so )?(?:sweet|thoughtful|kind) of you|grateful for you)\b")


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------
def detect(text: str, attribution_ok: bool = True) -> Dict[str, Any]:
    turns, labeled = parse_turns(text)
    labeled = labeled and attribution_ok
    found: Dict[str, Dict[str, str]] = {}
    if not labeled:
        return {"labeled": False, "found": found}

    idx = list(enumerate(turns))
    them = [(i, t) for i, (s, t) in idx if s == "them"]
    you = [(i, t) for i, (s, t) in idx if s == "you"]

    def after(you_pred, them_patterns, window=3):
        """First THEM hit within `window` turns after a YOU line matching you_pred."""
        anchors = [i for i, t in you if you_pred.search(t.lower())]
        for i, line, _ in _hits(them_patterns, them):
            if any(0 < i - a <= window for a in anchors):
                return i, line
        return None

    def add(key, line, speaker="them"):
        found.setdefault(key, {"speaker": speaker, "evidence": _quote(line)})

    # feelings_validated: validation answering a concern you raised
    h = after(YOU_CONCERN, VALIDATE)
    if h:
        add("feelings_validated", h[1])

    # concrete_plans: a plan phrase and a concrete time on the same THEM line,
    # or a concrete time given in answer to your question
    asked = [a for a, t in you if "?" in t]
    for i, t in them:
        low = t.lower()
        nxt = next((t2.lower() for j, t2 in them if j > i), "")
        if any(p.search(low) for p in DEFLECT_NEAR) or re.search(r"\b(?:can't|cannot|cant|not)\b", low) or RETRACT.search(low) or RETRACT.search(nxt):
            continue  # "can't tomorrow" names a day but is not a plan
        if CONCRETE_TIME.search(low) and (PLAN_VERB.search(low) or any(0 < i - a <= 2 for a in asked)):
            add("concrete_plans", t)
            break

    # offers_to_meet: they propose it themselves
    mh = _hits(MEET_OFFER, them)
    if mh:
        add("offers_to_meet", mh[0][1])

    # owned_apology
    for i, line, _ in _hits(OWNED, them):
        if not FAKE_APOLOGY.search(line.lower()):
            add("owned_apology", line)
            break

    # boundary_respected: you set a limit, they accept, and they don't push after
    anchors = [i for i, t in you if YOU_BOUNDARY.search(t.lower())]
    for i, line, _ in _hits(ACCEPT, them):
        a = [x for x in anchors if 0 < i - x <= 2]
        if a:
            pushed = any(ESCALATE_AFTER_NO.search(t.lower()) for j, t in them if a[0] < j <= a[0] + 4)
            if not pushed:
                add("boundary_respected", line)
                break

    # open_about_you
    oh = _hits(OPEN, them)
    if oh:
        add("open_about_you", oh[0][1])

    # asked_and_met: you ask plainly, they agree within 2 turns
    h = after(YOU_ASK, [AGREE], window=2)
    if h:
        add("asked_and_met", h[1])

    # curious_about_you: 2+ THEM questions about you
    def _curious(line):
        for sent in re.split(r"(?<=[.!])\s+", line.lower()):
            if CURIOUS_Q.search(sent.strip()) and ABOUT_YOU.search(sent):
                return True
        return False
    qs = [t for _, t in them if _curious(t)]
    if len(qs) >= 2:
        add("curious_about_you", qs[0])

    rh = _hits(REMEMBER, them)
    if rh:
        add("remembers_details", rh[0][1])

    h = after(YOU_GOOD_NEWS, [CELEBRATE], window=2)
    if h:
        add("celebrates_you", h[1])

    ah = [(i, t) for i, t in them if APPRECIATE.search(t.lower())]
    if ah:
        add("appreciation", ah[0][1])

    return {"labeled": True, "found": found}


# ---------------------------------------------------------------------------
# Payload builder
# ---------------------------------------------------------------------------
def _blocked_by_risk(payload: Dict[str, Any]) -> bool:
    if str(payload.get("lane") or "").upper() in BLOCKING_LANES:
        return True
    try:
        if int(payload.get("risk_score") or 0) >= 60:
            return True
    except (TypeError, ValueError):
        return True  # unreadable score: fail closed, show no positives
    concerns = {str(c).strip().lower() for c in _as_list(payload.get("concern_signals"))}
    concerns |= {str(c).strip().lower() for c in _as_list(payload.get("flags"))}
    return bool(concerns & MONEY_CONCERNS)


def _chips(payload: Dict[str, Any], voice: str, concern_keys: set, pattern_keys: set) -> List[Dict[str, str]]:
    try:
        from app.glossary import entry_for
    except Exception:  # glossary unavailable: degrade to titles only
        entry_for = None
    out = []
    for key in _as_list(payload.get("positive_signals")):
        if not isinstance(key, str) or key in pattern_keys:
            continue
        if CHIP_SUPPRESSED_BY.get(key) in concern_keys:
            continue
        e = entry_for(key, voice) if entry_for else None
        title = (e or {}).get("title") or key.replace("_", " ").strip().capitalize()
        out.append({"key": key, "title": title, "means": (e or {}).get("means", "")})
    return out


def build_healthy_patterns(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict) or not _eligible(payload):
        return None
    if _blocked_by_risk(payload):
        return None

    voice = CONNECTION if payload.get("presentation_mode") == CONNECTION else RISK
    subtle = payload.get("subtle_patterns") or {}
    concern_keys = set(subtle.get("keys") or []) if isinstance(subtle, dict) else set()

    text = payload.get("extracted_text")
    user_side = str(payload.get("user_side") or "").strip().lower()
    found = {}
    if isinstance(text, str) and text.strip():
        found = detect(text, attribution_ok=(user_side != "mix"))["found"]

    patterns = []
    for key in ORDER:
        if key not in found:
            continue
        if voice == RISK and key not in RISK_ELIGIBLE:
            continue
        c = COPY[key].get(voice)
        if not c:
            continue
        patterns.append({
            "key": key, "family": FAMILY[key], "title": c["title"], "body": c["body"],
            "evidence": found[key]["evidence"], "speaker": found[key]["speaker"],
        })

    coexist = []
    if voice == CONNECTION:
        titles = {p["key"]: p["title"] for p in patterns}
        for pos, neg in MIRROR_OF.items():
            if pos in titles and neg in concern_keys:
                neg_title = next((p["title"] for p in subtle.get("patterns", []) if p.get("key") == neg), neg)
                coexist.append(COEXIST_NOTE.format(pos=titles[pos].lower(), neg=neg_title.lower()))

    chips = _chips(payload, voice, concern_keys, {p["key"] for p in patterns}) if voice == CONNECTION else []

    if not patterns and not chips:
        return None
    return {
        "schema_version": SCHEMA_VERSION,
        "voice": voice,
        "heading": "What's going right" if voice == CONNECTION else "What lowers concern",
        "caveat": RISK_CAVEAT if voice == RISK else None,
        "patterns": patterns,
        "chips": chips,
        "coexist": coexist,
        "keys": [p["key"] for p in patterns],
    }
