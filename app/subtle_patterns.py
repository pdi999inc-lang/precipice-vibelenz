"""
app/subtle_patterns.py

VibeLenz subtle-pattern detector — deterministic, rule-based checks for moves
people commonly miss, either from inexperience or because they don't want to
see them.

Copyright © 2026 Ricky Sessums. All rights reserved.

WHAT IT DOES
  Reads the speaker-labelled conversation text (YOU: / THEM:) and reports six
  patterns, each with the line that triggered it:

    SAFETY tier   platform_migration_push   push to move off the app
                  secrecy_request           asks to keep things hidden
                  verification_dodging      repeated excuses to avoid video/meeting
    DYNAMICS tier future_faking             big future talk, near-term plans dodged
                  concern_minimized         your concern gets dismissed, not answered
    SELF tier     user_rationalizing        YOUR messages explain their behaviour away

WHY IT EXISTS
  Patterns 1-3 and 5 already appear as instructions inside the LLM prompt in
  app/analyzer_combined.py. That means they only fire when the LLM runs and
  chooses to report them. This module is the deterministic backstop: it runs in
  degraded mode too, and it records whether the LLM agreed (corroborated_by_llm).
  future_faking and user_rationalizing had no coverage anywhere.

SAFETY DECISIONS (deliberate; do not loosen without a written reason)
  - Additive only. This module never changes lane, risk_score, or flags. Wiring
    any of these into _risk_override() is a separate governance decision.
  - Fail closed on attribution. Direction-dependent patterns (concern_minimized,
    user_rationalizing) require YOU:/THEM: labels and a non-"mix" user_side.
    Without them they are not reported, rather than guessed.
  - Absent for BLOCKED (injection) and WITHHELD reads.
  - Runs in degraded mode on purpose: that is when the LLM coverage is missing.
  - Voice is split by lane. Connection copy never uses the clinical words in
    CLINICAL_WORDS; tests enforce it.
  - Pure functions. No DB, no network, no clock, no randomness.
  - Input capped at MAX_CHARS; all regexes are bounded (no nested quantifiers).

SOURCES FOR THE PATTERN DEFINITIONS (not for any training data)
  Off-app push, secrecy, verification excuses: FTC / FBI IC3 / FCC / ICE HSI
  public romance-scam guidance. Future faking: clinical and pop-psych
  literature (Psychology Today, PsychCentral). Minimization: emotional
  invalidation literature. User rationalizing: fraud-victimology research on
  victims rationalizing early warning signs (e.g. arXiv 2606.23241, Cross 2016,
  Kopp 2015). All example text in the eval set is synthetic.

INTEGRATION (app/main.py)
    from app.subtle_patterns import build_subtle_patterns
    payload["subtle_patterns"] = build_subtle_patterns(payload)   # inside try/except
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_VERSION = "subtle.v1"
MAX_CHARS = 20000
MAX_QUOTE = 120

CONNECTION = "connection"
RISK = "risk"

# Words reserved for risk mode by the product voice rule.
CLINICAL_WORDS = ("risk", "pressure", "danger", "flag", "signal", "elevated", "assessment")

# Deterministic key -> LLM keys that mean the same thing (for corroboration only).
LLM_EQUIVALENTS: Dict[str, Tuple[str, ...]] = {
    "platform_migration_push": ("platform_migration_early",),
    "secrecy_request": ("isolation_pressure_calling",),
    "verification_dodging": ("verification_avoidance", "deepfake_video_call_claim"),
    "future_faking": (),
    "concern_minimized": ("minimization", "gaslighting"),
    "user_rationalizing": ("intervention_resistance_high", "victim_state"),
}

TIER = {
    "platform_migration_push": "safety",
    "secrecy_request": "safety",
    "verification_dodging": "safety",
    "future_faking": "dynamics",
    "concern_minimized": "dynamics",
    "user_rationalizing": "self",
}

ORDER = [
    "secrecy_request",
    "verification_dodging",
    "platform_migration_push",
    "concern_minimized",
    "future_faking",
    "user_rationalizing",
]

# ---------------------------------------------------------------------------
# Copy. connection = thoughtful friend. risk = clinical.
# ---------------------------------------------------------------------------
COPY: Dict[str, Dict[str, Dict[str, str]]] = {
    "platform_migration_push": {
        CONNECTION: {
            "title": "They want to move the chat somewhere else",
            "body": "Switching apps isn't a problem on its own. But with someone you haven't met or video-called, getting you off the app early is the most common first step in a romance scam, because the app is what can report and ban them.",
            "next": "Stay on the app until you've had a live video call. Someone genuine won't mind waiting.",
        },
        RISK: {
            "title": "Off-platform migration request",
            "body": "Request to move the conversation to a separate messaging app. Consistent with FTC and FBI IC3 romance-fraud indicators: migration removes the originating platform's reporting and moderation controls.",
            "next": "Keep communication on the original platform until identity is verified by live video.",
        },
    },
    "secrecy_request": {
        CONNECTION: {
            "title": "They're asking you to keep this quiet",
            "body": "People who are good for you don't need to be hidden from the people who love you. Asking you to keep it secret, or suggesting your friends wouldn't get it, quietly cuts off the ones most likely to notice something's off.",
            "next": "Tell one person you trust about this conversation. Notice how it feels to do that.",
        },
        RISK: {
            "title": "Secrecy / isolation request",
            "body": "Instruction to conceal the relationship or conversation from third parties. Isolation from friends and family is a documented control tactic in romance fraud and coercive relationships.",
            "next": "Disclose the conversation to a trusted third party before any further commitment or payment.",
        },
    },
    "verification_dodging": {
        CONNECTION: {
            "title": "Seeing them keeps not happening",
            "body": "One broken camera is life. A run of reasons why a video call or meeting can't happen is a pattern, and it's the single most consistent tell in romance scams, because a live call is the one thing a fake profile can't survive.",
            "next": "Ask for a short video call this week. How they handle that ask tells you a lot.",
        },
        RISK: {
            "title": "Verification avoidance",
            "body": "Repeated deflection of live video or in-person contact. FBI, FCC and FTC guidance identify persistent refusal to video-call or meet as a primary romance-fraud indicator.",
            "next": "Require live video verification before continuing. Treat continued refusal as disqualifying.",
        },
    },
    "future_faking": {
        CONNECTION: {
            "title": "Big future, vague present",
            "body": "They paint a vivid picture of where this goes, but when it comes to something concrete soon, it slides. Real plans usually show up as small, specific steps, not just big promises.",
            "next": "Skip the someday. Ask for one specific plan with a day attached and see what comes back.",
        },
        RISK: {
            "title": "Future faking",
            "body": "Detailed long-range promises paired with deflection of near-term commitments. Documented as a manipulation tactic that secures present compliance with future rewards that do not materialize.",
            "next": "Weight near-term actions over stated intentions. Do not make present concessions based on promised futures.",
        },
    },
    "concern_minimized": {
        CONNECTION: {
            "title": "Your concern got waved off",
            "body": "You raised something and it got answered with 'you're overreacting' or similar, instead of an actual answer. You can be sensitive and still be right. The question you asked is still sitting there unanswered.",
            "next": "Say it once more, plainly, and ask for an answer to the thing itself, not a verdict on how you feel about it.",
        },
        RISK: {
            "title": "Concern minimization",
            "body": "The other party responds to a stated concern by characterizing the concern as excessive rather than addressing its substance. Classified as emotional invalidation; repeated use is associated with gaslighting.",
            "next": "Restate the original concern and require a substantive response.",
        },
    },
    "user_rationalizing": {
        CONNECTION: {
            "title": "You might be explaining them away",
            "body": "Across your own messages you talk yourself out of your concern, make excuses for them, or apologize for asking. That's really common, and it's often how people end up accepting less than they asked for.",
            "next": "Reread what you actually asked for. Was it unreasonable? If a friend asked for it, would you tell them they were too much?",
        },
        RISK: {
            "title": "Self-minimization in your messages",
            "body": "Your replies contain retractions of concern, excuses on the other party's behalf, or repeated apology for raising issues. Fraud-victimology research finds victims commonly rationalize early warning indicators; this read shows that pattern.",
            "next": "Evaluate the other party's conduct without the explanations you have supplied for them.",
        },
    },
}

COMBO_COPY = {
    "unverified_and_off_platform": {
        CONNECTION: "They're steering you off the app and also haven't let you see them. Together, that's the classic romance-scam setup.",
        RISK: "Combined: off-platform migration with verification avoidance. High-confidence romance-fraud configuration.",
    },
    "dismissed_then_retracted": {
        CONNECTION: "They waved off your concern, and then you took it back. Worth looking at who ended up doing the apologizing.",
        RISK: "Combined: concern minimization followed by self-retraction. The concern was withdrawn without being addressed.",
    },
}

NOTE_UNLABELED = {
    CONNECTION: "We couldn't tell who sent which message here, so check these against who actually said them. Some checks were skipped.",
    RISK: "Speaker attribution unavailable. Direction-dependent checks were not run; confirm sender for each item.",
}

# ---------------------------------------------------------------------------
# Patterns. All bounded; case-insensitive; applied to normalized lowercase text.
# ---------------------------------------------------------------------------
_APPS = r"(?:whats\s?app|telegram|signal app|signal|kik|hangouts?|google chat|wechat|viber|line app)"
_RX = lambda p: re.compile(p, re.IGNORECASE)

MIGRATION = [
    _RX(r"\b(?:add|text|message|msg|dm|find|hit|reach|chat with|talk to) me (?:up )?on " + _APPS),
    _RX(r"\b(?:move|switch|go|come|continue|talk|chat|talking|chatting)(?: this| our chat| the chat| over)? (?:to|on|over to) " + _APPS),
    _RX(r"\b(?:my|download|get|use|have|you on) " + _APPS + r"\b"),
    _RX(r"\b(?:easier|better|faster) to (?:chat|talk|text) (?:on|over|there)"),
    _RX(r"\bi(?:'m| am)? (?:not|rarely|hardly|barely) (?:on|use|check) (?:here|this app|this site|this)\b"),
    _RX(r"\b(?:about to|going to|gonna) delete (?:this|the) app\b"),
]
_NEG_MIGRATION = _RX(r"\b(?:don't|do not|never|won't|wouldn't|not)\b(?: \w+){0,3} " + _APPS)

# Prior in-person contact: moving apps after meeting is ordinary, not a scam setup.
MET_IN_PERSON = _RX(r"\b(?:we met (?:at|in|last)|met you at|nice meeting you|great seeing you|good seeing you|(?:so )?fun (?:last night|yesterday|tonight)|last night was|see you again|at (?:\w+'s|the) party)\b")

SECRECY = [
    _RX(r"\bkeep (?:this|it|us|that|our \w+) (?:between us|between you and me|private|secret|to yourself|quiet|on the low)"),
    _RX(r"\b(?:don't|do not|dont) (?:tell|mention|say anything to) (?:anyone|anybody|your|ur|the)\b"),
    _RX(r"\b(?:our (?:little )?secret|nobody (?:needs|has) to know|no one (?:needs|has) to know)"),
    _RX(r"\b(?:they|your friends|your family|your kids|people) (?:wouldn't|won't|would not|will not|don't|do not) understand (?:us|what we have|this)"),
    _RX(r"\b(?:your friends|your family|they)(?:'re| are) (?:just )?(?:jealous|trying to (?:split|come between|break) us)"),
    _RX(r"\bdelete (?:these|this|our|the) (?:messages|texts|chat|conversation|pics|photos)"),
]

VERIFY_STRONG = [
    _RX(r"\b(?:my )?(?:camera|cam|webcam|front camera)(?: is|'s)? (?:broken|not working|cracked|damaged|messed up|busted|acting up)"),
    _RX(r"\b(?:can't|cannot|can not|cant|unable to) (?:video ?call|video ?chat|facetime|face ?time|do video|call on video|turn on (?:my|the) camera)"),
    _RX(r"\b(?:bad|poor|weak|no|terrible) (?:connection|signal|network|internet|wifi|wi-fi|service) (?:here|out here|on the (?:rig|base|ship))"),
    _RX(r"\b(?:not|isn't|aren't|is not) allowed to (?:video|facetime|use (?:my|the) camera|make calls|call)"),
    _RX(r"\b(?:military|the base|base|command|the rig|rig|ship|company|contract|security)(?: rules)? (?:doesn't|does not|won't|will not|don't) (?:allow|let us|permit)"),
    _RX(r"\b(?:i'm|i am|im) (?:too )?(?:shy|not ready|not comfortable) (?:on|for|to) (?:camera|video|facetime|video ?call)"),
    _RX(r"\b(?:phone|screen)(?: is|'s)? (?:broken|cracked|damaged) so (?:i )?(?:can't|cannot|cant)"),
]
VERIFY_WEAK = [
    _RX(r"\bsomething came up\b"),
    _RX(r"\b(?:can't|cannot|cant) make it\b"),
    _RX(r"\b(?:maybe|probably) next (?:week|weekend|month|time)\b"),
    _RX(r"\bwhen i(?:'m| am)? (?:get )?back\b"),
    _RX(r"\bonce (?:my|this|the) (?:contract|deployment|mission|project|assignment) (?:ends|is over|is done|finishes)"),
    _RX(r"\b(?:not a good time|bad time) (?:for|to) (?:a )?(?:call|video|facetime)"),
]
ARCHETYPE = _RX(r"\b(?:oil rig|offshore|deployed|deployment|peace ?keeping|un mission|stationed (?:in|overseas|abroad)|overseas contract|on a ship|at sea|working abroad)\b")
YOU_ASK_VERIFY = _RX(r"\b(?:video ?call|facetime|face ?time|video ?chat|hop on (?:a )?call|see your face|meet (?:up|in person)|can we meet|when can we meet|want to meet)\b")

VISION = [
    _RX(r"\bwhen we (?:get married|move in|live together|are together|finally meet|finally see each other|start our life|have kids)"),
    _RX(r"\bour (?:house|home|future|wedding|kids|children|life together|forever)\b"),
    _RX(r"\bi(?:'ll| will| am going to|'m going to|m gonna|'m gonna) (?:take you|fly you|bring you|buy you|marry you|come for you|move to be with you|give you everything)"),
    _RX(r"\b(?:one day|someday|some day) (?:we|i|you)\b"),
    _RX(r"\bi (?:can see|picture|imagine|see) us\b"),
    _RX(r"\b(?:rest of (?:my|our) li(?:fe|ves)|grow old (?:together|with you)|forever with you|spend my life)\b"),
    _RX(r"\b(?:wife|husband) (?:material|someday)|future (?:wife|husband|mrs|mr)\b"),
]
DEFLECT_NEAR = [
    _RX(r"\bnot this (?:week|weekend|month)\b"),
    _RX(r"\b(?:can't|cannot|cant) (?:this|tomorrow|tonight|that day)\b"),
    _RX(r"\brain ?check\b"),
    _RX(r"\bwe(?:'ll| will) see\b"),
    _RX(r"\bwhen things (?:settle|calm down|slow down|get better)\b"),
    _RX(r"\bafter (?:this|my|the) (?:contract|deployment|project|trip|busy season|mission)\b"),
    _RX(r"\bplay it by ear\b"),
    _RX(r"\b(?:maybe|probably) next (?:week|weekend|month|time)\b"),
    _RX(r"\bi(?:'ll| will) let you know\b"),
    _RX(r"\b(?:soon|very soon)(?: baby| babe| love| my love| dear)?\s*(?:[.!?]|$)"),
    _RX(r"\bdon't rush (?:it|things|us)\b"),
]
YOU_ASK_NEAR = _RX(r"\b(?:when can (?:we|i)|when (?:will|are) (?:we|i|you)|are we still on|what day|this (?:weekend|week|friday|saturday|sunday)|tomorrow\?|tonight\?|can we (?:meet|see each other|hang out|get together)|when do i get to see you)\b")
CONCRETE_TIME = _RX(r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|tonight|\d{1,2}(?::\d{2})?\s?(?:am|pm)|at \d{1,2}\b|noon)\b")

MINIMIZE_STRONG = [
    _RX(r"\b(?:you're|you are|youre|ur|u r) (?:\w+ )?(?:overreacting|over reacting|too sensitive|so sensitive|being dramatic|so dramatic|being paranoid|paranoid|being crazy|crazy|imagining (?:things|it)|being ridiculous|insecure)"),
    _RX(r"\b(?:you're|you are|youre|stop|quit) (?:\w+ )?over ?thinking\b"),
    _RX(r"\bmaking (?:a big deal|this a big deal|a thing|something) out of (?:nothing|this)|making a big deal\b"),
    _RX(r"\breading (?:way )?too (?:much|deep) into\b"),
    _RX(r"\bthat (?:never|didn't) happen(?:ed)?\b"),
    _RX(r"\bwhy (?:are|r) you (?:always )?(?:making|so|being) (?:this|such|dramatic|sensitive|difficult)\b"),
]
MINIMIZE_WEAK = [
    _RX(r"\b(?:calm down|relax|chill out|chill|get over it|let it go)\b"),
    _RX(r"\bit(?:'s| is) not (?:that deep|a big deal|that serious|a thing)\b"),
    _RX(r"\bnot this again\b"),
]
YOU_CONCERN = _RX(r"\b(?:i (?:felt|feel)|it (?:hurt|bothered|upset)|that (?:hurt|bothered|upset)|i(?:'m| am| was) (?:upset|hurt|confused|bothered)|i didn't like|it bothers me|why (?:did|didn't|were|are|would) you|can we talk about|i noticed|you (?:said|told me|promised))\b")

RATIONALIZE = [
    _RX(r"\bi know (?:you(?:'re| are)|you've been|he(?:'s| is)|she(?:'s| is)|they(?:'re| are)) (?:\w+ )?(?:busy|stressed|tired|going through|swamped)"),
    _RX(r"\b(?:you(?:'re| are)|he(?:'s| is)|she(?:'s| is)|they(?:'re| are)) (?:probably|prob|just|prolly) (?:\w+ )?(?:busy|stressed|tired|going through|overwhelmed)"),
    _RX(r"\b(?:it's|its|it is) not (?:your|his|her|their) fault\b"),
    _RX(r"\b(?:you|he|she|they) didn't mean (?:it|to)\b"),
    _RX(r"\bmaybe i(?:'m| am) (?:just )?(?:overreacting|over reacting|overthinking|being (?:dramatic|needy|paranoid|silly|sensitive|too much|crazy))"),
    _RX(r"\bi(?:'m| am|m) (?:probably|prob|prolly|just) (?:overreacting|overthinking|being (?:dramatic|needy|paranoid|silly|sensitive|crazy|too much))"),
    _RX(r"\bsorry (?:for|about) (?:being|getting|acting) (?:so )?(?:needy|annoying|clingy|dramatic|a lot|too much|weird|emotional|crazy)"),
    _RX(r"\bi (?:don't|dont|do not) (?:mean|want) to (?:nag|bother you|be (?:needy|annoying|a bother|clingy|a lot|pushy))"),
    _RX(r"\bi shouldn't have (?:asked|said anything|brought (?:it|that) up|said that)\b"),
    _RX(r"\b(?:never ?mind|nvm|forget i said (?:anything|that)|forget it)\b"),
    _RX(r"\byou don't (?:have|need) to explain\b"),
]
APOLOGY = _RX(r"\b(?:sorry|i apologize|my bad)\b")

_LABEL = re.compile(r"^\s*(you|them)\s*:\s*(.*)$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return [value]
        return parsed if isinstance(parsed, list) else [parsed]
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return []


def _norm(s: str) -> str:
    s = s.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    return re.sub(r"\s+", " ", s).strip()


def parse_turns(text: str) -> Tuple[List[Tuple[str, str]], bool]:
    """Return ([(speaker, line)], labeled). speaker in {'you','them','unknown'}."""
    text = (text or "")[:MAX_CHARS]
    turns: List[Tuple[str, str]] = []
    labeled = False
    current = "unknown"
    for raw in text.splitlines():
        line = _norm(raw)
        if not line:
            continue
        m = _LABEL.match(line)
        if m:
            labeled = True
            current = m.group(1).lower()
            body = m.group(2).strip()
            if body:
                turns.append((current, body))
        else:
            turns.append((current, line))
    return turns, labeled


def _hits(patterns, lines):
    out = []
    for i, line in lines:
        low = line.lower()
        for p in patterns:
            if p.search(low):
                out.append((i, line, p.pattern))
    return out


def _quote(line: str) -> str:
    line = line.strip()
    return line if len(line) <= MAX_QUOTE else line[: MAX_QUOTE - 1].rstrip() + "\u2026"


def _llm_keys(payload: Dict[str, Any]) -> set:
    keys = set()
    for field in ("flags", "concern_signals"):
        for item in _as_list(payload.get(field)):
            if isinstance(item, str):
                keys.add(re.sub(r"[\s\-]+", "_", item.strip().lower()))
    phase = payload.get("phase")
    if isinstance(phase, str) and phase.strip().upper() == "VICTIM_STATE":
        keys.add("victim_state")
    return keys


def _eligible(payload: Dict[str, Any]) -> bool:
    if payload.get("injection_blocked"):
        return False
    lane = str(payload.get("lane") or "").upper()
    if lane == "BLOCKED":
        return False
    level = str(payload.get("risk_level") or payload.get("risk_label") or "").upper()
    if level == "WITHHELD":
        return False
    return True


# ---------------------------------------------------------------------------
# Detection (pure, text-only; payload-free for easy testing)
# ---------------------------------------------------------------------------
def detect(text: str, attribution_ok: bool = True) -> Dict[str, Any]:
    """
    Returns {"labeled": bool, "found": {key: {"speaker":..., "evidence":...}}}.
    attribution_ok=False forces unlabeled handling (e.g. user_side == "mix").
    """
    turns, labeled = parse_turns(text)
    labeled = labeled and attribution_ok
    if not labeled:
        turns = [("unknown", t) for _, t in turns]

    indexed = list(enumerate(turns))
    them = [(i, t) for i, (s, t) in indexed if s == "them"]
    you = [(i, t) for i, (s, t) in indexed if s == "you"]
    other = them if labeled else [(i, t) for i, (_, t) in indexed]
    other_speaker = "them" if labeled else "unknown"

    found: Dict[str, Dict[str, Any]] = {}

    def add(key, line, speaker):
        found[key] = {"speaker": speaker, "evidence": _quote(line)}

    # 1a. platform migration (suppressed when either side references meeting in person)
    met = any(MET_IN_PERSON.search(t.lower()) for _, (_s, t) in indexed)
    for i, line, _ in ([] if met else _hits(MIGRATION, other)):
        if _NEG_MIGRATION.search(line.lower()):
            continue
        add("platform_migration_push", line, other_speaker)
        break

    # 1b. secrecy
    sec = _hits(SECRECY, other)
    if sec:
        add("secrecy_request", sec[0][1], other_speaker)

    # 2. verification dodging
    strong = _hits(VERIFY_STRONG, other)
    weak = _hits(VERIFY_WEAK, other)
    archetype = any(ARCHETYPE.search(t.lower()) for _, t in other)
    you_asks = sum(1 for _, t in you if YOU_ASK_VERIFY.search(t.lower()))
    strong_lines = {i for i, _, _ in strong}
    if (
        len(strong_lines) >= 2
        or (strong and archetype)
        or (labeled and you_asks >= 1 and len(strong_lines) + len({i for i, _, _ in weak}) >= 2)
        or (labeled and you_asks >= 2 and strong)
    ):
        ev = strong[0][1] if strong else weak[0][1]
        add("verification_dodging", ev, other_speaker)

    # 5. future faking
    vision = _hits(VISION, other)
    deflect = _hits(DEFLECT_NEAR, other)
    vision_lines = {i for i, _, _ in vision}
    if len(vision_lines) >= 2 or (len(vision) >= 2):
        you_near = [i for i, t in you if YOU_ASK_NEAR.search(t.lower())]
        them_concrete = [i for i, t in them if CONCRETE_TIME.search(t.lower())]
        unanswered = bool(you_near) and not any(c > min(you_near) for c in them_concrete)
        if deflect or (labeled and unanswered):
            add("future_faking", (deflect[0][1] if deflect else vision[0][1]), other_speaker)

    if labeled:
        # 6. concern minimized
        ms = _hits(MINIMIZE_STRONG, them)
        mw = _hits(MINIMIZE_WEAK, them)
        concern_idx = [i for i, t in you if YOU_CONCERN.search(t.lower())]
        # Structural rule: minimization is a RESPONSE to a concern you raised.
        # Without a prior concern line, "you're overthinking it" is often support.
        ms_after = [h for h in ms if any(c < h[0] for c in concern_idx)]
        mw_after = [h for h in mw if any(c < h[0] for c in concern_idx)]
        if ms_after or mw_after:
            src = ms_after[0] if ms_after else mw_after[0]
            add("concern_minimized", src[1], "them")

        # 10. user rationalizing
        rh = _hits(RATIONALIZE, you)
        apologies = sum(len(APOLOGY.findall(t.lower())) for _, t in you)
        score = len({(i, p) for i, _, p in rh}) + (1 if apologies >= 3 else 0)
        if score >= 2:
            ev = rh[0][1] if rh else next(t for _, t in you if APOLOGY.search(t.lower()))
            add("user_rationalizing", ev, "you")

    return {"labeled": labeled, "found": found}


# ---------------------------------------------------------------------------
# Payload builder
# ---------------------------------------------------------------------------
def build_subtle_patterns(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the subtle-pattern card for this read, or None when nothing applies."""
    if not isinstance(payload, dict) or not _eligible(payload):
        return None
    text = payload.get("extracted_text")
    if not isinstance(text, str) or not text.strip():
        return None

    user_side = str(payload.get("user_side") or "").strip().lower()
    result = detect(text, attribution_ok=(user_side != "mix"))
    found = result["found"]
    if not found:
        return None

    voice = CONNECTION if payload.get("presentation_mode") == CONNECTION else RISK
    llm = _llm_keys(payload)

    patterns = []
    for key in ORDER:
        if key not in found:
            continue
        c = COPY[key][voice]
        patterns.append({
            "key": key,
            "tier": TIER[key],
            "title": c["title"],
            "body": c["body"],
            "next_step": c["next"],
            "evidence": found[key]["evidence"],
            "speaker": found[key]["speaker"],
            "corroborated_by_llm": any(k in llm for k in LLM_EQUIVALENTS[key]),
        })

    combos = []
    if "platform_migration_push" in found and "verification_dodging" in found:
        combos.append({"key": "unverified_and_off_platform", "text": COMBO_COPY["unverified_and_off_platform"][voice]})
    if "concern_minimized" in found and "user_rationalizing" in found:
        combos.append({"key": "dismissed_then_retracted", "text": COMBO_COPY["dismissed_then_retracted"][voice]})

    return {
        "schema_version": SCHEMA_VERSION,
        "voice": voice,
        "labeled": result["labeled"],
        "note": None if result["labeled"] else NOTE_UNLABELED[voice],
        "heading": "Easy to miss" if voice == CONNECTION else "Rule-based pattern checks",
        "patterns": patterns,
        "combos": combos,
        "safety_present": any(p["tier"] == "safety" for p in patterns),
        "keys": [p["key"] for p in patterns],
    }
