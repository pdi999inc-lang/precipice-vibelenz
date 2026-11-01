"""
app/gottman_patterns.py - observational conflict / connection markers (Gottman-informed).
SHADOW MODE ONLY. Patterns, not clinical labels.

Copyright © 2026 Ricky Sessums. All rights reserved.

Origin: spec drafted with Grok (T8 v3); code reviewed, corrected and extended by Claude
after a line-by-line run found it could never fire in production (relationship_type was
read from a payload that does not carry it), had a stub safety gate, and had several
marker defects. See GOTTMAN_NOTES in the delivery bundle.

SHADOW-MODE CONTRACT (do not loosen without a written reason)
  - Output goes to the AUDIT RECORD ONLY (key "gottman"). It is never added to the user
    payload and never changes lane, risk_score, flags or any other field.
  - build_gottman_patterns() NEVER raises. Any failure returns None.
  - The audit value holds counts and turn indexes only. NO conversation text.
  - Kill switch GOTTMAN_SHADOW_ENABLED is read at call time. Changing the variable in
    Railway restarts the service, so it takes effect on restart, not instantly.
  - Deterministic regex + turn structure. No LLM, no I/O, no new dependency.

WHAT THIS IS NOT
  - Nothing here is validated. Marker lists, the bid/turned-toward rules, MIN_BIDS and
    TURN_TOWARD_RATIO are design judgement with no labeled data behind them. They WILL
    misclassify (sarcasm, banter, teasing, quoting, cultural register).
  - Tier labels are design judgement, not measured severity.
  - Gottman's observational research was on committed couples; "match" is the weakest fit.
  - Never quote a detection rate for this module.

SCOPE
  - Concern patterns (THEM lines only, one pattern per line, precedence in this order):
      contempt > criticism > defensiveness > stonewalling.
  - repair_attempt: both speakers, evaluated only on lines that fired no concern pattern.
  - bid_responsiveness / low_turn_toward: conversation level, YOU bids, the very next turn.
  - style_similarity: a METRIC (ratio of recent message lengths). It never fires.
  - phubbing_proxy: deferred (no reliable text evidence in screenshots).

GATES (any one returns None): kill switch off; not a dict; ineligible read
  (injection_blocked / lane BLOCKED / WITHHELD); lane in BLOCKING_LANES (FRAUD,
  COERCION_RISK); user_side "mix"; relationship_type not in current_partner / ex / match;
  any safety-tier subtle pattern; fewer than 2 labeled turns for either speaker.
  Positives (repair_attempt, bid_responsiveness) are additionally suppressed whenever
  healthy_patterns._blocked_by_risk() says so.

AUDIT VALUE: schema_version, module_version, config_version, keys, counts (capped at 20),
  evidence_indexes (indexes into the list returned by app.subtle_patterns.parse_turns,
  capped at 5 per key), turns_evaluated, status ("ok" | "capped"), and optionally
  style_similarity_ratio.

DAG: subtle_patterns -> healthy_patterns -> gottman (shadow) -> ... -> write_audit_record.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

from app.healthy_patterns import BLOCKING_LANES, _blocked_by_risk
from app.subtle_patterns import _RX, _eligible, parse_turns

SCHEMA_VERSION = "gottman.v1"
GOTTMAN_VERSION = "1.0.0"
CONFIG_VERSION = "gottman-cfg-2026-10-31-a"

ALLOWED_RELATIONSHIP_TYPES = frozenset({"current_partner", "ex", "match"})
MIN_TURNS_PER_SPEAKER = 2
MIN_BIDS = 3               # unvalidated
TURN_TOWARD_RATIO = 0.6    # unvalidated
MAX_INDEXES_PER_KEY = 5
MAX_COUNT_PER_KEY = 20
MAX_LINE_CHARS = 500       # per-line matching window; bounds regex cost on hostile input
STONEWALL_MIN_WORDS = 6    # preceding YOU turn must be longer than 5 words

TIERS = {  # design judgement, not measured severity
    "contempt": "HIGH",
    "criticism": "MEDIUM-HIGH",
    "defensiveness": "MEDIUM",
    "stonewalling": "MEDIUM",
    "low_turn_toward": "MEDIUM",
    "repair_attempt": "POSITIVE",
    "bid_responsiveness": "POSITIVE",
}
POSITIVE_KEYS = frozenset({"repair_attempt", "bid_responsiveness"})

# ---------------------------------------------------------------------------
# Marker families (all unvalidated)
# ---------------------------------------------------------------------------
CONTEMPT_MARKERS = [
    _RX(r"\blol\s*ok(?:ay)?\b"),
    _RX(r"\boh,? poor (?:baby|you)\b"),
    _RX(r"\bwhatever you say\b"),
    _RX(r"\beye[\s-]?roll"),
    _RX("\U0001F644"),  # rolling-eyes emoji matched literally (\b cannot anchor an emoji)
]

CRITICISM_MARKERS = [
    _RX(r"\byou (?:always|never)\b"),
    _RX(r"\bwhat(?:'s| is) wrong with you\b"),
    _RX(r"\byou(?:'re| are) (?:so |such a |just )?(?:selfish|lazy|stupid|crazy|dramatic|childish|pathetic|useless)\b"),
    _RX(r"\byou don'?t (?:even )?care\b"),
]
# "you always/never" followed by a positive tail is a compliment, not criticism.
CRITICISM_POSITIVE_TAIL = _RX(
    r"\byou (?:always|never)\b[^.!?]{0,30}\b"
    r"(?:laugh|smile|amaz|surpris|fail to|let me down|there for me|inspire)"
)
# Negation in the clause before the marker ("I'm not saying you never listen").
CRITICISM_NEGATED = _RX(
    r"\b(?:not|never said|didn'?t say|don'?t think|isn'?t true|aren'?t)\b[^.!?]{0,25}\byou (?:always|never)\b"
)
# Quoting / reporting someone else's words.
QUOTED_SPAN = re.compile(r"[\"“][^\"”]*[\"”]")
REPORTED = _RX(r"\b(?:said|says|told me|texted|wrote)\b[^.!?]{0,25}\b(?:you|that you)\b")

DEFENSIVE_MARKERS = [
    _RX(r"\b(?:yes|yeah),? but\b"),
    _RX(r"\bit(?:'s| is) not my fault\b"),
    _RX(r"\bmaybe if you\b"),
    _RX(r"\bwhat was i supposed to do\b"),
    _RX(r"\bi can'?t ever do anything right\b|\bi can never do anything right\b"),
]

# Stonewalling: a bare disengaged reply. Spec list only: ok, k, fine, whatever.
STONEWALL_REPLY = _RX(r"^(?:ok|okay|k|kk|fine|whatever)\s*[.!]*$")

EMOTIONAL_CONTENT = _RX(
    r"\b(?:feel|felt|feeling|hurt|hurts|sad|angry|upset|worried|scared|lonely|overwhelmed|"
    r"talk about|need to talk|need to understand|what happened|left out|ignored|miss you)\b"
)

REPAIR_MARKERS = [
    _RX(r"\bcan we (?:start over|try that again)\b"),
    _RX(r"\bi(?:'m| am) sorry (?:that )?(?:it |that )?came out wrong\b"),
    _RX(r"\bi(?:'m| am) sorry,? i\b"),
    _RX(r"\bi need (?:a few |some |20 |twenty |thirty |ten )?minutes\b"),
    _RX(r"\bwe(?:'re| are) on the same team\b"),
    _RX(r"\blet me try that again\b"),
]

# --- bids -----------------------------------------------------------------
BID_MARKERS = [
    _RX(r"\bhow (?:was|is) your (?:day|night|morning)\b"),
    _RX(r"\bhow are you (?:doing|feeling|holding up)\b"),
    _RX(r"\bi(?:'m| am) (?:really |so |very )?(?:tired|sad|stressed|worried|hurt|lonely|upset|overwhelmed|anxious)\b"),
    _RX(r"\bcan we (?:talk|chat|catch up)\b"),
    _RX(r"\bdo you have a (?:minute|sec|second)\b"),
    _RX(r"\bi (?:miss you|need to talk|felt|feel)\b"),
    _RX(r"\bi had a (?:hard|rough|long|bad) day\b"),
    _RX(r"\blook at this\b"),
]
# Unambiguous logistics only. A line with emotional content is never treated as logistics.
LOGISTICS = _RX(r"\b(?:what time|pick you up|pick up|address|how much|which (?:store|one)|errand|parking)\b")

# --- turned-toward --------------------------------------------------------
DISENGAGED = _RX(r"^(?:ok|okay|k|kk|whatever|idk|lol|hm+|mhm|nm|nvm)\s*[.!?]*$")
REFUSAL = _RX(r"\b(?:don'?t want to talk|not now|leave me alone|stop texting|not in the mood|can'?t talk)\b")
ACK_MARKERS = _RX(
    r"\b(?:tell me more|i hear you|that sounds|want to talk|i'?m sorry|i understand|i'?m here|"
    r"what happened|are you ok|you ok|me too|same here|i get it)\b"
)
SAME_ONLY = _RX(r"^same\s*[.!]*$")
TALK_BID = _RX(r"\bcan we (?:talk|chat|catch up)\b|\bdo you have a (?:minute|sec|second)\b|\bneed to talk\b")
ACCEPT = _RX(r"^(?:yes|yeah|yep|sure|of course|ready|go ahead|give me|i can|now works|that works)\b")
HOW_QUESTION = _RX(r"\bhow (?:was|is) your (?:day|night|morning)\b|\bhow are you\b")
DIRECT_ANSWER = _RX(r"^(?:fine|good|great|long|rough|busy|tired|alright|not bad|pretty good)\b")


def _kill_switch_enabled() -> bool:
    return os.environ.get("GOTTMAN_SHADOW_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")


def _words(line: str) -> int:
    return len(line.split())


def _clip(line: str) -> str:
    return line[:MAX_LINE_CHARS]


# ---------------------------------------------------------------------------
# Line classifiers
# ---------------------------------------------------------------------------
def _is_contempt(low: str) -> bool:
    return _words(low) <= 6 and any(p.search(low) for p in CONTEMPT_MARKERS)


def _is_criticism(low: str) -> bool:
    stripped = QUOTED_SPAN.sub(" ", low)  # words inside quotes are someone else's
    if not any(p.search(stripped) for p in CRITICISM_MARKERS):
        return False
    if CRITICISM_POSITIVE_TAIL.search(stripped) or CRITICISM_NEGATED.search(stripped):
        return False
    if REPORTED.search(stripped):
        return False
    return True


def _is_defensive(low: str) -> bool:
    stripped = QUOTED_SPAN.sub(" ", low)
    return any(p.search(stripped) for p in DEFENSIVE_MARKERS)


def _is_bid(line: str) -> bool:
    low = _clip(line).lower()
    if not any(p.search(low) for p in BID_MARKERS):
        return False
    if LOGISTICS.search(low) and not EMOTIONAL_CONTENT.search(low):
        return False
    return True


def _is_turned_toward(bid_line: str, reply_line: str) -> bool:
    """
    Deterministic and UNVALIDATED; it will misclassify.
    True when the very next THEM turn engages the bid:
      - never when it is a refusal or a bare disengaged reply (Ok / K / Whatever / idk);
      - a question of 2+ words, an acknowledgment marker, "same"/"me too";
      - for a how-was-your-day bid, a direct answer (Fine / Good / Long / ...);
      - for an invitation to talk, an acceptance (Yes / Sure / Of course ...).
    Content-word overlap is deliberately NOT used: it rewards "I don't want to talk".
    """
    low = _clip(reply_line).lower().strip()
    bid = _clip(bid_line).lower()
    if not low or REFUSAL.search(low) or DISENGAGED.match(low):
        return False
    if SAME_ONLY.match(low) or ACK_MARKERS.search(low):
        return True
    if "?" in low and _words(low) >= 2:
        return True
    if HOW_QUESTION.search(bid) and DIRECT_ANSWER.match(low):
        return True
    if TALK_BID.search(bid) and ACCEPT.match(low):
        return True
    return False


# ---------------------------------------------------------------------------
# Detection (pure; text only)
# ---------------------------------------------------------------------------
def detect(text: str) -> Dict[str, Any]:
    """Return per-key hit indexes (full, uncapped) plus structure. Holds no text."""
    turns, labeled = parse_turns(text or "")
    out: Dict[str, Any] = {"labeled": labeled, "turns": len(turns), "hits": {}, "bids": 0,
                           "turned": 0, "not_turned": 0, "style_ratio": None}
    if not labeled:
        return out
    hits: Dict[str, List[int]] = {}

    def add(key: str, idx: int) -> None:
        hits.setdefault(key, []).append(idx)

    for i, (spk, line) in enumerate(turns):
        low = _clip(line).lower().strip()
        fired = False
        if spk == "them":
            if _is_contempt(low):
                add("contempt", i); fired = True
            elif _is_criticism(low):
                add("criticism", i); fired = True
            elif _is_defensive(low):
                add("defensiveness", i); fired = True
            elif STONEWALL_REPLY.match(low) and i > 0 and turns[i - 1][0] == "you":
                prev = _clip(turns[i - 1][1])
                if _words(prev) >= STONEWALL_MIN_WORDS and EMOTIONAL_CONTENT.search(prev.lower()):
                    add("stonewalling", i); fired = True
        if not fired and spk in ("you", "them") and any(p.search(low) for p in REPAIR_MARKERS):
            add("repair_attempt", i)

    # Bids: YOU bids and the very next turn.
    turned_idx: List[int] = []
    not_turned_idx: List[int] = []
    for i, (spk, line) in enumerate(turns):
        if spk != "you" or not _is_bid(line):
            continue
        nxt = turns[i + 1] if i + 1 < len(turns) else None
        if nxt is not None and nxt[0] == "them" and _is_turned_toward(line, nxt[1]):
            turned_idx.append(i)
        else:
            not_turned_idx.append(i)
    total = len(turned_idx) + len(not_turned_idx)
    out["bids"], out["turned"], out["not_turned"] = total, len(turned_idx), len(not_turned_idx)
    if total >= MIN_BIDS:
        if len(turned_idx) / total >= TURN_TOWARD_RATIO:
            hits["bid_responsiveness"] = turned_idx
        elif len(not_turned_idx) / total >= TURN_TOWARD_RATIO:
            hits["low_turn_toward"] = not_turned_idx

    # Style similarity: a metric only. Never a pattern.
    you_len = [_words(t) for s, t in turns if s == "you"]
    them_len = [_words(t) for s, t in turns if s == "them"]
    if len(you_len) >= 3 and len(them_len) >= 3:
        you_avg = sum(you_len[-3:]) / 3
        them_avg = sum(them_len[-3:]) / 3
        if them_avg > 0:
            out["style_ratio"] = round(you_avg / them_avg, 3)
    out["hits"] = hits
    out["speakers"] = (sum(1 for s, _ in turns if s == "you"), sum(1 for s, _ in turns if s == "them"))
    return out


def _safety_present(payload: Dict[str, Any]) -> bool:
    subtle = payload.get("subtle_patterns")
    if not isinstance(subtle, dict):
        return False
    if subtle.get("safety_present"):
        return True
    for p in subtle.get("patterns") or []:
        if isinstance(p, dict) and p.get("tier") == "safety":
            return True
    return False


def build_gottman_patterns(payload: Dict[str, Any], relationship_type: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Shadow builder. Returns the audit-shaped dict or None. Never mutates payload, never raises.
    relationship_type must be passed in by the caller: the analysis payload does not carry it.
    """
    try:
        if not _kill_switch_enabled():
            return None
        if not isinstance(payload, dict) or not _eligible(payload):
            return None
        if str(payload.get("lane") or "").upper() in BLOCKING_LANES:
            return None
        if str(payload.get("user_side") or "").strip().lower() == "mix":
            return None
        rel = str(relationship_type if relationship_type is not None else payload.get("relationship_type") or "")
        if rel.strip().lower() not in ALLOWED_RELATIONSHIP_TYPES:
            return None
        if _safety_present(payload):
            return None
        text = payload.get("extracted_text")
        if not isinstance(text, str) or not text.strip():
            return None

        det = detect(text)
        if not det["labeled"]:
            return None
        you_n, them_n = det.get("speakers", (0, 0))
        if you_n < MIN_TURNS_PER_SPEAKER or them_n < MIN_TURNS_PER_SPEAKER:
            return None

        positives_blocked = _blocked_by_risk(payload)
        keys: List[str] = []
        counts: Dict[str, int] = {}
        evidence: Dict[str, List[int]] = {}
        capped = False
        for key, idxs in det["hits"].items():
            if key in POSITIVE_KEYS and positives_blocked:
                continue
            if not idxs:
                continue
            keys.append(key)
            counts[key] = min(len(idxs), MAX_COUNT_PER_KEY)
            evidence[key] = idxs[:MAX_INDEXES_PER_KEY]
            if len(idxs) > MAX_COUNT_PER_KEY or len(idxs) > MAX_INDEXES_PER_KEY:
                capped = True
        if not keys:
            return None

        result: Dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "module_version": GOTTMAN_VERSION,
            "config_version": CONFIG_VERSION,
            "keys": sorted(keys),
            "counts": counts,
            "evidence_indexes": evidence,
            "turns_evaluated": det["turns"],
            "status": "capped" if capped else "ok",
        }
        if det["style_ratio"] is not None:
            result["style_similarity_ratio"] = det["style_ratio"]
        return result
    except Exception:  # noqa: BLE001 - shadow mode must never raise
        return None
