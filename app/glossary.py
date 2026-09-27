"""
app/glossary.py

VibeLenz pattern glossary — the field guide behind the chips.

Copyright © 2026 Ricky Sessums. All rights reserved.

WHAT IT DOES
  Maps analyzer signal keys to a short, plain-language card: a friendly title,
  what it means, and why it matters. build_glossary() returns the entries
  relevant to one read, marked seen/unseen, so the Analytics tab can show
  progress ("7 of 21 patterns seen") without inventing any new data.

WHY THIS IS NOT GAMIFICATION THEATRE
  Progress is over PATTERNS LEARNED, never over conversations uploaded. There
  is no streak, no score, no reward for analyzing more. Someone who never
  returns loses nothing.

SAFETY DECISIONS (deliberate, do not loosen without a written reason)
  - Voice is split by lane, per the product voice rule. Connection-mode
    entries read like a friend; risk-mode entries stay clinical. A signal that
    appears in both gets the tone of the lane it is being shown in.
  - No storage. Seen-state lives in the browser (localStorage), so this module
    holds no per-person data and adds no table, no cookie read, no retention
    surface. Clearing the browser clears progress; that is the accepted cost.
  - Unknown signal keys degrade to a neutral derived title with no body copy,
    rather than guessing at a meaning.
  - Pure functions. No DB, no network, no clock. Safe to call anywhere.

INTEGRATION (app/main.py)
    from app.glossary import build_glossary
    payload["glossary"] = build_glossary(payload)   # inside try/except
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "glos.v1"
_SIGNAL_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

CONNECTION = "connection"
RISK = "risk"

# key -> (lane_voice, title, what_it_means, why_it_matters)
# lane_voice: CONNECTION = warm/humanistic, RISK = clinical/scanner.
_ENTRIES: Dict[str, Dict[str, str]] = {
    # ---------------- connection-mode patterns ----------------
    "reciprocal_engagement": {
        "voice": CONNECTION,
        "title": "Effort goes both ways",
        "means": "You both ask questions, pick up threads, and keep things moving. Neither person is carrying it alone.",
        "matters": "It is the most reliable sign that interest is mutual. One-sided effort is the thing that quietly wears people down.",
    },
    "warm_reception_present": {
        "voice": CONNECTION,
        "title": "They're warm when you reach out",
        "means": "When you start something, they meet you there instead of giving you a flat reply.",
        "matters": "Warmth on arrival is harder to fake than enthusiasm in the middle. It tells you where you actually sit.",
    },
    "playful_engagement_present": {
        "voice": CONNECTION,
        "title": "There's play in it",
        "means": "Teasing, jokes, running bits — the conversation has room to not be serious.",
        "matters": "Play usually means both people feel safe. It tends to disappear first when something is off.",
    },
    "sexual_reciprocity_present": {
        "voice": CONNECTION,
        "title": "The flirting goes both ways",
        "means": "Attraction is being expressed by both of you, at roughly the same pace.",
        "matters": "Mutual is the part that matters. One person pushing while the other absorbs is a different pattern entirely.",
    },
    "high_intent_present": {
        "voice": CONNECTION,
        "title": "They're being clear about wanting something",
        "means": "Direct language about seeing you, making plans, or where this is going.",
        "matters": "Clarity is worth noticing on its own. Most ambiguity people agonize over is just nobody saying the plain thing.",
    },
    "vision_building_present": {
        "voice": CONNECTION,
        "title": "They talk about later",
        "means": "References to future plans, shared things, or a version of this that continues.",
        "matters": "Genuine future talk builds slowly and survives contact with logistics. Watch whether it ever turns into a date on a calendar.",
    },
    "repair_attempt_present": {
        "voice": CONNECTION,
        "title": "A rough patch got repaired",
        "means": "Something landed wrong, and one of you moved to fix it rather than letting it sit.",
        "matters": "How people repair predicts how a relationship holds up better than never arguing does. Friction is normal; what counts is the response to it.",
    },
    "initial_confusion_present": {
        "voice": CONNECTION,
        "title": "It started out muddled",
        "means": "Early crossed wires, misread tone, or a rocky opening.",
        "matters": "On its own it means little. What matters is whether it cleared up, which is why it is usually read alongside repair.",
    },
    "confusion_then_repair": {
        "voice": CONNECTION,
        "title": "Muddled, then sorted out",
        "means": "An early misunderstanding that the two of you actually worked through.",
        "matters": "This is a good sign, not a bad one. It shows the conversation can survive a bump.",
    },
    "boundary_language_present": {
        "voice": CONNECTION,
        "title": "Someone named a limit",
        "means": "One of you said what you do or do not want, plainly.",
        "matters": "Naming a limit is healthy. The thing to watch is what happens next — whether it is respected or argued with.",
    },
    "hard_rejection_present": {
        "voice": CONNECTION,
        "title": "A clear no was given",
        "means": "Someone declined something without hedging.",
        "matters": "A clear no is information, not failure. Continued pursuit after one is what turns it into a problem.",
    },
    "sexual_directness": {
        "voice": CONNECTION,
        "title": "Explicit talk showed up",
        "means": "Sexual content entered the conversation directly.",
        "matters": "Not a problem in itself. Timing is what carries meaning — especially how early it arrived and whether both people moved there together.",
    },

    # ---------------- risk-mode patterns ----------------
    "pressure_present": {
        "voice": RISK,
        "title": "Pressure",
        "means": "Repeated pushing after hesitation, a no, or a non-answer.",
        "matters": "Pressure is the mechanism most manipulation runs on. It is present in nearly every coercive exchange, regardless of the surface topic.",
    },
    "fear_driven_urgency": {
        "voice": RISK,
        "title": "Manufactured urgency",
        "means": "An artificial deadline or consequence used to compress your decision time.",
        "matters": "Urgency exists to prevent verification. A legitimate request survives you taking a day to check it.",
    },
    "money_request": {
        "voice": RISK,
        "title": "Request for money",
        "means": "A direct or indirect ask for funds, transfers, gift cards, or covering a cost.",
        "matters": "Financial asks from someone you have not met in person are the single highest-value signal in fraud detection.",
    },
    "trust_calibration_small_ask": {
        "voice": RISK,
        "title": "Small ask first",
        "means": "A minor, low-cost request that tests whether you will comply.",
        "matters": "Small asks are reconnaissance. The amount is not the point; your response to it is what gets measured.",
    },
    "lure_and_pivot": {
        "voice": RISK,
        "title": "Lure and pivot",
        "means": "Attention, flattery, or intimacy front-loaded, then a switch to a request.",
        "matters": "The warm phase exists to make the ask harder to refuse. The pivot point is the tell.",
    },
    "vulnerability_narrative_early": {
        "voice": RISK,
        "title": "Early hardship story",
        "means": "A personal crisis disclosed unusually early in the exchange.",
        "matters": "Premature disclosure manufactures obligation and sympathy before trust has been earned. It frequently precedes a financial ask.",
    },
    "blame_inversion": {
        "voice": RISK,
        "title": "Blame inversion",
        "means": "Your reasonable question or boundary is reframed as the offense.",
        "matters": "It trains you to stop asking. Over time it removes your ability to raise concerns at all.",
    },
    "plan_collapse_blame_inversion": {
        "voice": RISK,
        "title": "Plans collapse, fault redirected",
        "means": "Arrangements fall through repeatedly, and the failure is placed on you.",
        "matters": "The pattern matters more than any single cancellation. Look at the cumulative count, not the current excuse.",
    },
    "credential_or_sensitive_info_signal": {
        "voice": RISK,
        "title": "Request for sensitive information",
        "means": "An ask for codes, logins, identity documents, or account details.",
        "matters": "No legitimate party needs a verification code you received. This request has effectively one purpose.",
    },
    "payment_before_verification": {
        "voice": RISK,
        "title": "Payment before verification",
        "means": "Money is requested before you can confirm the person, property, or item is real.",
        "matters": "Reversing the normal order is the defining structure of advance-fee fraud.",
    },
    "verification_path_shift": {
        "voice": RISK,
        "title": "Verification path moved",
        "means": "The method of confirming legitimacy changes once you try to use it.",
        "matters": "A moving verification target means there is nothing at the end of it.",
    },
    "owner_identity_shift": {
        "voice": RISK,
        "title": "Identity changed",
        "means": "Who the counterparty claims to be is not consistent across the exchange.",
        "matters": "Inconsistent identity claims are a hard fraud indicator, not a memory lapse.",
    },
    "property_identity_shift": {
        "voice": RISK,
        "title": "Listing details changed",
        "means": "The property, item, or offer described is not consistent across the exchange.",
        "matters": "Shifting details usually mean the listing was copied from a real one that the sender does not control.",
    },
    "withheld_owner_verification": {
        "voice": RISK,
        "title": "Verification refused",
        "means": "Reasonable proof of identity or ownership is declined or deflected.",
        "matters": "A legitimate counterparty can verify. Refusal, rather than inability, is the signal.",
    },
}


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return []
    return list(value) if isinstance(value, (list, tuple)) else []


def fallback_title(key: str) -> str:
    base = key[:-8] if key.endswith("_present") else key
    return base.replace("_", " ").strip().capitalize()


def entry_for(key: str, lane_voice: str) -> Optional[Dict[str, str]]:
    """One glossary card, or a title-only card for an unknown key."""
    if not isinstance(key, str) or not _SIGNAL_RE.match(key):
        return None
    e = _ENTRIES.get(key)
    if not e:
        return {"key": key, "title": fallback_title(key), "means": "", "matters": "", "known": False}
    # Voice guard: never show clinical copy inside connection mode.
    if lane_voice == CONNECTION and e["voice"] == RISK:
        return {"key": key, "title": fallback_title(key), "means": "", "matters": "", "known": False}
    return {"key": key, "title": e["title"], "means": e["means"], "matters": e["matters"], "known": True}


def total_for_voice(lane_voice: str) -> int:
    return sum(1 for e in _ENTRIES.values() if e["voice"] == lane_voice)


def build_glossary(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Pure and deterministic. Returns the glossary cards for the signals in this
    read, plus the catalogue size for progress display. None when there is
    nothing to explain.
    """
    if not isinstance(payload, dict):
        return None
    lane_voice = CONNECTION if str(payload.get("presentation_mode") or "") == CONNECTION else RISK

    keys: List[str] = []
    for field in ("positive_signals", "concern_signals", "flags"):
        for s in _as_list(payload.get(field)):
            if isinstance(s, str) and _SIGNAL_RE.match(s) and s not in keys:
                keys.append(s)
    if not keys:
        return None

    cards = [c for c in (entry_for(k, lane_voice) for k in keys) if c]
    known = [c for c in cards if c["known"]]
    if not known:
        return None
    return {
        "version": SCHEMA_VERSION,
        "voice": lane_voice,
        "entries": known,
        "catalogue_total": total_for_voice(lane_voice),
    }
