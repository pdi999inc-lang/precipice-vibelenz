"""
safety_floors.py — deterministic, raise-only safety floors + lane/score coherence. ENFORCED.

Copyright (c) 2026 Ricky Sessums. All rights reserved.

WHY (evidence, 2026-11-01 eval)
  - Backup (deterministic) engine missed 17/17 synthetic scams: gift-card rent,
    crypto deposits, extortion all scored 0-6 / BENIGN. It is what users get
    whenever the AI call fails (it did today: API credit ran out).
  - AI path: investment pitches without an explicit "send me" came back
    COERCION 62 on one run and BENIGN 58 on the next (same text); isolation +
    secrecy came back BENIGN 58. A BENIGN lane next to a MEDIUM score is the
    same class of contradiction as the HIGH-vs-NONE result page.

DAG PLACEMENT
  analyze_text -> _apply_physical_harm_override -> apply_safety_floors -> interpreter.
  Runs on BOTH the AI path and the deterministic fallback.

MVP SCOPE (only what the other party says; the user's own lines never trigger)
  F1 money_request_floor     they ask the user to send money / gift cards / codes /
                             crypto / a deposit or fee          -> FRAUD, score >= 60
  F2 investment_pitch_floor  trading/crypto/platform talk + a starting deposit
                                                                -> FRAUD, score >= 70
  F3 isolation_secrecy_floor "keep this between us" + pushing away friends/family
                                                                -> COERCION_RISK, score >= 60
  F4 leverage_threat_floor   photos/screenshots + exposure threat -> COERCION_RISK, >= 75
  F5 money_mule_floor        reshipping packages / moving payments for them -> FRAUD, >= 60
  F7 surveillance_floor      covert access to a phone or messages, a demand for a
                             passcode, location used as an accusation -> COERCION_RISK, >= 60
  F8 financial_control_floor cutting off access to money, seizing an account, or a
                             one-way permission structure -> COERCION_RISK, >= 60
  F6 partner_isolation_floor ultimatum / prohibition / alienation aimed at the
                             target's friends or family, no secrecy required
                                                                -> COERCION_RISK, >= 60
  Family/friend reads: a plain "can you lend me $50" does NOT fire F1; it fires
  only with a classic scam tell (gift cards, codes, crypto, wire, new account).
  L1 lane correction        COERCION_RISK + a money/investment floor (and no coercion
                             floor) -> FRAUD. Moves between risk lanes only.
  C1 lane/score coherence    risk lane with score < 35 -> score 35;
                             score >= 35 in a non-risk lane -> risk_level matches
                             score (protective read) + flag lane_score_conflict

INVARIANTS
  - Raise-only: never lowers a score, never moves a read out of a risk lane,
    never touches BLOCKED or physical-harm reads.
  - Deterministic, no network, no new dependency.
  - Any internal error returns the input unchanged plus safety_floors_error
    (the read is never blocked by this module; the existing fallback/degraded
    handling still applies).

DEGRADED BEHAVIOR
  Runs identically in degraded mode — that is the point: the fallback engine is
  where floors matter most.

AUDIT
  result["safety_floors"] = {"fired": [...], "coherence": "...", "before": {...}}
  (ids and numbers only, no conversation text). main.py's audit record already
  carries `signals` (flags), which now include the floor ids.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

SCHEMA_VERSION = "floors.v1"

RISK_LANES = {"FRAUD", "COERCION_RISK"}
SKIP_LANES = {"BLOCKED"}

# ---------------------------------------------------------------- speaker split
_LABEL = re.compile(r"^\s*(YOU|THEM|LEFT|RIGHT|ME)\s*:\s*(.*)$", re.I)


def other_party_text(text: str) -> str:
    """Lines spoken by the other party. If the text carries YOU/THEM labels, only
    THEM lines count. If attribution is unknown (no labels, or LEFT/RIGHT only),
    every line counts — fail closed."""
    lines = (text or "").splitlines()
    has_you_them = any(re.match(r"^\s*(YOU|THEM)\s*:", l, re.I) for l in lines)
    if not has_you_them:
        return "\n".join(lines)
    out = []
    for l in lines:
        m = _LABEL.match(l)
        if m and m.group(1).upper() == "THEM":
            out.append(m.group(2))
    return "\n".join(out)


# ---------------------------------------------------------------- patterns
_SEND = r"(?:send|sending|wire|transfer|venmo|zelle|cash\s*app|paypal|pay|cover|lend|loan|help\s+(?:me\s+)?with)"
_MONEY_OBJ = (r"(?:money|\$\s?\d[\d,]*|\d{2,6}\s*(?:dollars|bucks|usd|usdt)|gift\s*cards?|the\s+codes?|"
              r"bitcoin|btc|crypto|usdt|deposit|(?:customs|release|processing|holding|training|materials|"
              r"activation|transfer|application|clearance)\s+fee|the\s+fee|a\s+fee|small\s+amount|fee\s+(?:of|is)\s+\$?\d)")
F1_PATTERNS = [
    re.compile(_SEND + r"\b[^.?!\n]{0,40}?\b" + _MONEY_OBJ, re.I),
    re.compile(r"\b(?:send|give)\s+(?:me\s+)?the\s+codes?\b", re.I),
    re.compile(r"\bgift\s*cards?\b[^.?!\n]{0,40}\b(?:rent|deposit|pay|keys|fee)", re.I),
    re.compile(r"\b(?:rent|deposit|keys|fee)\b[^.?!\n]{0,40}\bgift\s*cards?\b", re.I),
    re.compile(r"\b(?:deposit|holding\s+fee|fee)\b[^.?!\n]{0,40}\b(?:to\s+hold|to\s+reserve|to\s+lock|before\s+(?:the\s+)?(?:viewing|tour))", re.I),
    re.compile(r"\b(?:could|can|would)\s+you\s+(?:send|help|cover|lend)\b[^.?!\n]{0,40}\b(?:\$?\d{2,}|amount|money|fee|bills?)", re.I),
    re.compile(r"\bonly\s+\$?\d{2,5}\b[^.?!\n]{0,40}\b(?:pay\s+you\s+back|repay|refund)", re.I),
    # "800 to reserve it", "1500 to hold it"
    re.compile(r"\$?\b\d{3,5}\b\s+to\s+(?:reserve|hold|lock\s+(?:it\s+)?in|secure)\b", re.I),
    # verification-code takeover: "read it back to me", "send me the code"
    re.compile(r"\b(?:\d[\s-]?digit\s+)?code\b[^.?!\n]{0,60}\b(?:read\s+it\s+back|send\s+it\s+(?:back|to\s+me)|tell\s+me\s+(?:what\s+it\s+says|the\s+code))", re.I),
    re.compile(r"\b(?:read|send|give|tell)\s+(?:me\s+)?(?:the|that|your)\s+(?:\d[\s-]?digit\s+)?(?:verification\s+)?code\b", re.I),
    # withdrawal/unlock fee: "pay the tax before you can withdraw", "separate deposit to release"
    re.compile(r"\b(?:tax|fee|deposit|payment)\b[^\n]{0,80}\b(?:before\s+(?:you\s+can\s+)?withdraw\w*|to\s+(?:withdraw|unlock|release|unfreeze)\b)", re.I),
    re.compile(r"\b(?:withdraw\w*|unlock|release)\b[^\n]{0,80}\b(?:separate\s+deposit|pay\s+(?:the\s+)?(?:tax|fee)|account\s+(?:gets|will\s+be)\s+frozen)", re.I),
    # business email compromise: changed bank / new account
    re.compile(r"\b(?:changed|new|updated|different)\s+(?:banks?|account|bank\s+(?:details|info|account)|routing|wire\s+(?:details|instructions))\b", re.I),
]
# Ask + amount anywhere in the other party's lines ("would you be able to help with that? ... just 800")
F1_ASK = re.compile(
    r"\b(?:(?:would|could|can)\s+you\s+(?:be\s+able\s+to\s+)?(?:help|send|cover|lend|spot)|"
    r"help\s+(?:me\s+)?(?:out\s+)?with\s+(?:that|this|it|the)|i\s+hate\s+(?:to\s+ask|asking)|"
    r"(?:only|just)\s+(?:this\s+once|a\s+small\s+amount)|you'?re\s+the\s+only\s+(?:one|person)\s+i\s+trust)", re.I)
F1_AMOUNT = re.compile(r"(?:\$\s?\d[\d,]*|\b\d{2,5}\b(?!\s*(?:hours?|minutes?|days?|weeks?|months?|years?|am|pm|%|messages?|miles?))|small\s+amount|a\s+couple\s+hundred|\bfee\b)", re.I)
F1_MONEYCTX = re.compile(r"\b(?:fee|bills?|transfer|bank|customs|money|pay\s+(?:you\s+)?back|repay|medicine|hospital|deposit|account)\b", re.I)
# deposit/fee you "get back" (task scams)
F1_REFUND = re.compile(r"\b(?:deposit|fee|starter\s+balance|starting\s+balance)\b[^\n]{0,120}\b(?:get\s+(?:it\s+)?back|refund\w*|comes\s+back|with\s+profit|make\s+\$?\d+\s+back)", re.I)

F_MULE = re.compile(
    r"\b(?:reship\w*|re-?ship\w*|forward\s+(?:the\s+)?(?:packages?|items?|money|payments?)|"
    r"accept\s+the\s+delivery\s+and\s+send|payments?\s+between\s+accounts|receive\s+(?:money|funds|payments?)\s+(?:for\s+us|and\s+send))\b", re.I)

F_LEVERAGE_OBJ = re.compile(r"\b(?:pictures?|photos?|pics?|screenshots?|videos?|messages)\b", re.I)
F_LEVERAGE_THREAT = re.compile(
    r"(?:people\s+(?:talk|should\s+know)|get(?:s)?\s+out\b|leak\w*|post\s+(?:them|it)|send\s+(?:them|it)\s+to|"
    r"your\s+(?:job|boss|family|friends?|wife|husband)|don'?t\s+make\s+me|what\s+happens\s+if\s+(?:you\s+)?(?:don'?t|they\s+don'?t)|"
    r"stopping\s+doesn'?t\s+(?:always\s+)?mean\s+it\s+ends|everyone\s+will\s+see)", re.I)

_INVEST = r"(?:crypto|bitcoin|btc|eth|usdt|tether|stablecoin|trading|trades|platform|signals?|mining|defi|forex|invest\w*|returns?|portfolio|mentor|digital\s+assets|vip\s+level|profit\s+from\s+(?:our|the)\s+orders?)"
_DEPOSIT = (r"(?:deposit|start(?:ing)?\s+(?:with|balance|amount)|put\s+(?:a\s+little\s+)?in|starter\s+balance|"
            r"(?:account|wallet)\s+(?:is\s+)?(?:active|exceeds|over)|minimum|first\s+trade|to\s+start|"
            r"sponsor\s+you|walk\s+you\s+through)")
F2_INVEST = re.compile(r"\b" + _INVEST + r"\b", re.I)
F2_DEPOSIT = re.compile(r"\b" + _DEPOSIT + r"\b", re.I)
F2_AMOUNT = re.compile(r"(?:\$\s?\d[\d,]*|\b\d{2,6}\b\s*(?:to\s+start|dollars|usd|usdt|-\s?\d{2,6})|\b\d{2,6}\s*(?:to|-)\s*\d{2,6}\b|\b[1-9]\d{0,2}k\b)", re.I)
F2_AMOUNT_JOIN = re.compile(r"\b(?:at\s+least\s+\$?\d{3,6}|\$?\d{3,6}\s+to\s+(?:join|start|open|activate|enter|qualify)|(?:start|begin)\s+with\s+\$?\d{3,6}|(?:add|put\s+in|deposit)\s+\$?\d{3,6})\b", re.I)
F2_GAIN = re.compile(r"\b(?:doubled|tripled|earned\s+(?:more\s+than\s+)?\$?\d|made\s+a\s+plan\s+of|\d{1,3}\s?%\s+return|returns?\s+(?:have\s+been|were)|made\s+(?:enough|more|\$?\d)|profit\w*|earnings)\b", re.I)
F2_WORDAMOUNT = re.compile(r"\b(?:a\s+)?(?:couple|few)\s+hundred\b|\b\d+\s+grand\b", re.I)

F3_SECRECY = re.compile(
    r"\b(?:keep\s+(?:this|it|us)\s+(?:between\s+us|to\s+ourselves|quiet|secret)|"
    r"don'?t\s+tell\s+(?:anyone|anybody|your\s+(?:family|friends?|mom|dad|sister|brother))|"
    r"between\s+us\s+(?:a\s+little\s+)?(?:longer|for\s+now))\b", re.I)
F3_ISOLATE = re.compile(
    r"\b(?:other\s+people\s+(?:complicate|ruin|mess)|they\s+don'?t\s+(?:know|understand)\s+what\s+we\s+have|"
    r"(?:want\s+to|always)\s+insert\s+themselves|don'?t\s+listen\s+to\s+(?:your\s+)?(?:family|friends?)|"
    r"(?:they'?re|they\s+are)\s+(?:just\s+)?jealous|outside\s+opinions|"
    r"you'?re\s+the\s+only\s+(?:one|person)\s+who\s+(?:gets|understands|listens)|"
    r"just\s+(?:the\s+two\s+of\s+us|us)\s+right\s+now|"
    r"you\s+don'?t\s+need\s+(?:them|anyone\s+else))\b", re.I)

# F6: isolation in an intimate-partner context. The F3 pair above is built for
# romance-scam isolation, where secrecy ("keep this between us") and dismissal of
# outsiders ("they're just jealous") co-occur. Partner isolation carries no secrecy:
# the other relationships are attacked in the open. Verified 2026-11-22 against the
# coercion fixtures: F3 fired on none of three partner-isolation reads, including an
# explicit "it's them or me".
#
# High precision on purpose. Every alternative is a phrase that is hard to say without
# coercive intent. Early ambiguous framing ("i feel weird after you see her") is
# deliberately NOT matched: that read needs context the analysis engine has and a regex
# does not, and a floor that adjudicates ambiguity is the wrong instrument. The floor is
# the deterministic backstop for the unambiguous cases, which is what a degraded read
# (LLM unavailable) otherwise misses entirely.
F6_PARTNER_ISOLATION = re.compile(
    r"(?:"
    # ultimatum: forcing a choice between the speaker and the target's people.
    # A bare "me or her" is ordinary speech ("who is driving, me or her"), so the
    # framing is required: the "it's X or Y" copula, or an explicit demand to choose.
    r"\bit'?s\s+(?:me\s+or\s+(?:her|him|them|your\s+\w+)|(?:her|him|them|your\s+\w+)\s+or\s+me)\b|"
    r"\b(?:me\s+or\s+(?:her|him|them|your\s+\w+)|(?:her|him|them|your\s+\w+)\s+or\s+me)\s*[,.!]?\s*(?:figure\s+it\s+out|choose|chose|pick\s+one|decide|your\s+call|thats\s+it)\b|"
    r"\bchoos(?:e|ing)\s+between\s+me\s+and\b|"
    # alienation framing: the third party is cast as an enemy of the relationship
    r"\b(?:turning|turned|poisoning|poisoned)\s+you\s+against\s+me\b|"
    # loyalty test attached to cutting contact
    r"\bif\s+you\s+(?:actually\s+|really\s+|truly\s+)?cared\b[^.!?]{0,60}?\b(?:stop|stopped|quit)\s+(?:seeing|talking\s+to|speaking\s+to)\b|"
    # explicit prohibition on contact
    r"\bi\s+(?:forbid\s+you|don'?t\s+want\s+you)\s+(?:to\s+)?(?:see|seeing|talk\s+to|talking\s+to|around)\s+(?:her|him|them|your\s+\w+)\b"
    r")", re.I)

# F7: surveillance and device control in a partner context. Verified 2026-11-22 that
# no floor fired on monitoring reads at any intensity, including an admitted read of
# private messages and a demand for the phone passcode.
#
# Only covert access and demands are matched. Ordinary visibility between partners
# ("i saw your story", "whats the wifi password", "did you get home") is not
# surveillance and must not fire: the discriminator is going through something, or
# demanding access to it, rather than being shown it.
F7_SURVEILLANCE = re.compile(
    r"(?:"
    # covert access, already carried out
    r"\b(?:went|going|go)\s+through\s+your\s+(?:phone|messages|texts?|dms?|emails?|chats?|things)\b|"
    r"\bi\s+(?:read|checked|looked\s+at|saw)\s+your\s+(?:dms?|private\s+messages?|texts?\s+with|messages?\s+with|search\s+history|call\s+log)\b|"
    # demanding the keys to a device or account
    r"\b(?:send|give|tell)\s+me\s+your\s+(?:passcode|pin|phone\s+password|unlock\s+code)\b|"
    r"\bunlock\s+your\s+phone\s+(?:for\s+me|and\s+(?:show|give|hand))\b|"
    r"\bnothing\s+to\s+hide\b[^.!?]{0,40}?\b(?:passcode|password|phone|show\s+me)\b|"
    r"\b(?:passcode|password|phone)\b[^.!?]{0,40}?\bnothing\s+to\s+hide\b|"
    # location tracking used as an accusation
    r"\byour\s+location\s+(?:stopped|was\s+off|showed|says|said|hasn'?t)\b|"
    r"\bi\s+(?:track|check)\s+your\s+location\b|"
    # dictating custody of the device
    r"\byour\s+phone\s+(?:stays|goes|on\s+the\s+counter|in\s+the\s+kitchen)\b"
    r")", re.I)

# F8: financial control in a partner context. Verified 2026-11-22 that no floor fired,
# including on a read where a card was frozen while the other person needed a
# prescription.
#
# Only one-directional control is matched. Shared budgeting between partners
# ("lets check with each other on big purchases", "can we talk before we spend") is
# ordinary and must not fire: the discriminator is one party holding the access.
F8_FINANCIAL_CONTROL = re.compile(
    r"(?:"
    # cutting off access
    r"\bi\s+(?:froze|cancelled|canceled|blocked|closed|cut\s+off)\s+(?:your|the)\s+(?:card|account|cards|access)\b|"
    r"\bi\s+took\s+you\s+off\s+(?:the|our)\s+account\b|"
    # seizing control of shared money
    r"\b(?:im|i\s+am|ill|i\s+will)\s+moving\s+the\s+account\s+to\s+my\s+name\b|"
    r"\b(?:your|the)\s+(?:paycheck|wages|salary)\s+goes?\s+(?:in)?to\s+(?:my|our)\s+account\b[^.!?]{0,40}?\bi\s+manage\b|"
    # permission structure over the other party's spending
    r"\byou(?:'?ll|\s+will)?\s+(?:just\s+)?ask\s+me\s+when\s+you\s+need\s+(?:money|anything|something)\b|"
    r"\bask\s+me\s+(?:first\s+)?before\s+you\s+(?:spend|buy|order|withdraw)\b|"
    r"\byou\s+(?:need|have\s+to\s+get)\s+my\s+permission\s+to\s+(?:spend|buy|withdraw)\b|"
    r"\bi\s*'?ll\s+give\s+you\s+an\s+allowance\b|"
    r"\byou\s+run\s+(?:things|everything|it)\s+past\s+me\s+first\b"
    r")", re.I)

# Tier B for F8. Neither half is coercive on its own - partners do divide the
# bookkeeping, and "ask me first" is ordinary about plans - so they are required
# together: a money context AND a one-way permission structure over it.
F8_MONEY_CTX = re.compile(
    r"\b(?:account|accounts|card|cards|spending|money|paycheck|wages|salary|allowance|finances|funds)\b", re.I)
F8_PERMISSION = re.compile(
    r"(?:"
    r"\bask\s+me\s+(?:first|next\s+time|when\s+you\s+need)\b|"
    r"\bwhich\s+i\s+manage\b|\bi\s+manage\s+(?:it|them|that)\b|"
    r"\bi\s+handle\s+(?:it|the\s+money|that)\b|"
    r"\bmy\s+permission\b|"
    r"\buntil\s+we\s+(?:talk|discuss)\b"
    r")", re.I)


def detect(text: str, relationship_type: str = "") -> List[str]:
    """Return fired floor ids for the other party's lines."""
    t = other_party_text(text)
    relationship_type = str(relationship_type or "").lower()
    fired: List[str] = []
    if F2_INVEST.search(t) and F2_DEPOSIT.search(t) and (
            F2_AMOUNT.search(t) or F2_WORDAMOUNT.search(t) or F2_GAIN.search(t) or F2_AMOUNT_JOIN.search(t)):
        fired.append("investment_pitch_floor")
    if (any(p.search(t) for p in F1_PATTERNS)
            or (F1_ASK.search(t) and F1_AMOUNT.search(t) and F1_MONEYCTX.search(t))
            or F1_REFUND.search(t)):
        if not (relationship_type in _CLOSE_TIES and not _hard_money_tell(t)):
            fired.append("money_request_floor")
    if F_MULE.search(t):
        fired.append("money_mule_floor")
    if F_LEVERAGE_OBJ.search(t) and F_LEVERAGE_THREAT.search(t):
        fired.append("leverage_threat_floor")
    if F3_SECRECY.search(t) and F3_ISOLATE.search(t):
        fired.append("isolation_secrecy_floor")
    if F6_PARTNER_ISOLATION.search(t):
        fired.append("partner_isolation_floor")
    if F7_SURVEILLANCE.search(t):
        fired.append("surveillance_floor")
    if F8_FINANCIAL_CONTROL.search(t) or (F8_MONEY_CTX.search(t) and F8_PERMISSION.search(t)):
        fired.append("financial_control_floor")
    return fired


_CLOSE_TIES = {"family", "friend"}
_HARD_TELL = re.compile(r"\b(?:gift\s*cards?|the\s+codes?|crypto|bitcoin|usdt|wire|western\s+union|customs|verification\s+code|new\s+account|changed\s+banks?)\b", re.I)


def _hard_money_tell(t: str) -> bool:
    """A family/friend asking to borrow money is ordinary; only fire for them when
    the request carries a classic scam tell (gift cards, codes, crypto, wire...)."""
    return bool(_HARD_TELL.search(t))


# ---------------------------------------------------------------- apply
_FLOOR_TARGET: Dict[str, Tuple[str, int]] = {
    # order = precedence when the read is not yet in a risk lane
    "leverage_threat_floor": ("COERCION_RISK", 75),
    "investment_pitch_floor": ("FRAUD", 70),
    "money_request_floor": ("FRAUD", 60),
    "money_mule_floor": ("FRAUD", 60),
    "isolation_secrecy_floor": ("COERCION_RISK", 60),
    "partner_isolation_floor": ("COERCION_RISK", 60),
    "surveillance_floor": ("COERCION_RISK", 60),
    "financial_control_floor": ("COERCION_RISK", 60),
}


def _level(score: int) -> str:
    return "HIGH" if score >= 70 else ("MEDIUM" if score >= 35 else "LOW")


def apply_safety_floors(result: Dict[str, Any], text: str, relationship_type: str = "") -> Dict[str, Any]:
    if not isinstance(result, dict):
        return result
    lane0 = str(result.get("lane") or "")
    if lane0 in SKIP_LANES or result.get("physical_harm_override"):
        return result
    try:
        out = dict(result)
        score0 = int(out.get("risk_score", 0) or 0)
        before = {"lane": lane0, "risk_score": score0, "risk_level": out.get("risk_level")}
        fired = detect(text, relationship_type)
        score, lane = score0, lane0

        for fid in sorted(fired, key=list(_FLOOR_TARGET).index):
            tlane, tscore = _FLOOR_TARGET[fid]
            score = max(score, tscore)
            if lane not in RISK_LANES:
                lane = tlane          # first fired floor wins; order = precedence

        # Lane correction within risk lanes: a money/investment floor with no coercion
        # floor means the AI's COERCION label is the wrong risk lane (eval: crypto pitch
        # came back COERCION 62-70). Still a risk read either way; FRAUD copy fits.
        _fraud_floors = {"investment_pitch_floor", "money_request_floor", "money_mule_floor"}
        _coercion_floors = {"leverage_threat_floor", "isolation_secrecy_floor",
                            "partner_isolation_floor", "surveillance_floor",
                            "financial_control_floor"}
        if lane == "COERCION_RISK" and (set(fired) & _fraud_floors) and not (set(fired) & _coercion_floors) \
                and not out.get("physical_harm_override"):
            lane = "FRAUD"

        coherence = "ok"
        if lane in RISK_LANES and score < 35:
            score = 35
            coherence = "risk_lane_low_score_raised"
        elif lane not in RISK_LANES and score >= 35:
            coherence = "lane_score_conflict"

        changed = fired or coherence != "ok"
        if not changed:
            return result

        out["risk_score"] = score
        lvl = _level(score)
        cur = str(out.get("risk_level") or "LOW").upper()
        order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
        out["risk_level"] = lvl if order.get(lvl, 0) >= order.get(cur, 0) else cur
        out["lane"] = lane

        add = list(fired) + (["lane_score_conflict"] if coherence == "lane_score_conflict" else [])
        flags = [f for f in (out.get("flags") or []) if f not in ("No signals detected", "No concerning patterns detected")]
        out["flags"] = list(dict.fromkeys(flags + add))
        if fired:
            sigs = [str(x) for x in (out.get("key_signals") or []) if x and x != "No signals detected"]
            out["key_signals"] = list(dict.fromkeys(sigs + fired))
            try:
                from app.analyzer_combined import _score_evidence
                out["evidence_scoring"] = _score_evidence(out["key_signals"])
            except Exception:  # noqa: BLE001
                pass
            if lane in RISK_LANES:
                # A floor-made risk read must not carry reassuring chips.
                out["positive_signals"] = []
                out["key_dampeners"] = []
                out["alternative_explanations"] = []
            if out.get("vie_action") in (None, "", "NONE", "ALLOW"):
                out["vie_action"] = "WARN"
        out["safety_floors"] = {"schema": SCHEMA_VERSION, "fired": fired, "coherence": coherence, "before": before}
        return out
    except Exception as e:  # noqa: BLE001
        bad = dict(result)
        bad["safety_floors"] = {"schema": SCHEMA_VERSION, "error": type(e).__name__}
        return bad
