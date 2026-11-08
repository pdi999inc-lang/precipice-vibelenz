from __future__ import annotations

import json
import logging
import os
import re as _re
from typing import Any, Dict, List

logger = logging.getLogger("vibelenz.reply_engine")

# ---------------------------------------------------------------------------
# reply_engine.py — VibeLenz Reply Suggestion Engine
# ---------------------------------------------------------------------------
# Generates 2-3 realistic text message suggestions the user can copy and send.
# Lane-gated: FRAUD/COERCION_RISK suppress normal suggestions.
# Gender-aware: adjusts tone, directness, pacing based on other_gender.
# Fail-closed: any error returns empty list — never blocks the analysis.
# ---------------------------------------------------------------------------

_GATED_LANES = {"FRAUD", "COERCION_RISK"}

_SAFETY_REPLIES = {
    "FRAUD": {"tone": "safety", "text": "ask them to verify their identity independently before continuing this conversation"},
    "COERCION_RISK": {"tone": "safety", "text": "state your boundary clearly one time. if they push back, that's your answer"},
}

_DEFAULT_SAFETY = {"tone": "safety", "text": "slow down and verify independently before responding"}


def _detect_reply_mode(extracted_text: str) -> str:
    """
    Determine whether to generate a reply to their last message or a general
    next-move suggestion. Scans from the end of the text for speaker labels.
    Fails closed to 'next_move' if labels are ambiguous or absent.
    """
    lines = [l.strip() for l in (extracted_text or "").strip().split("\n") if l.strip()]
    for line in reversed(lines):
        upper = line.upper()
        if upper.startswith("THEM:"):
            return "reply"
        if upper.startswith("YOU:"):
            return "next_move"
    return "next_move"


def _is_gated(payload: dict) -> bool:
    """Check if this analysis should suppress normal reply suggestions."""
    lane = str(payload.get("lane", "")).upper()
    if lane in _GATED_LANES:
        return True
    if payload.get("extraction_present"):
        return True
    if payload.get("pressure_present"):
        return True
    return False


def _safety_reply(payload: dict) -> dict:
    """Return the single protective suggestion for gated lanes."""
    lane = str(payload.get("lane", "")).upper()
    reply = _SAFETY_REPLIES.get(lane, _DEFAULT_SAFETY)
    return {
        "suggested_replies": [reply],
        "reply_mode": "safety",
        "replies_suppressed": True,
        "replies_suppressed_reason": f"{lane} lane active — normal suggestions suppressed",
    }


def _build_system_prompt() -> str:
    return (
        "You are VibeLenz's reply assistant. You generate realistic text message suggestions "
        "that the user can copy and send. You write like a real person texting — not like an AI.\n\n"
        "CRITICAL RULES:\n"
        "- Every suggestion must be under 25 words\n"
        "- Write in lowercase unless emphasis genuinely requires caps\n"
        "- Use fragments, contractions, casual abbreviations naturally\n"
        "- No formal punctuation (no semicolons, colons, em-dashes)\n"
        "- Drop trailing periods on short messages\n"
        "- No 'I would like to' or 'I was wondering if' — these are AI tells\n"
        "- No emoji unless the conversation already uses them\n"
        "- No 'haha' as nervous filler — only if genuinely funny\n"
        "- No starting with 'Hey!' — that's a newsletter opener\n"
        "- Match the energy level of the conversation\n"
        "- Each suggestion must feel like a different person wrote it\n\n"
        "GENDER-AWARE RULES:\n"
        "When user is texting a WOMAN:\n"
        "- Playful: confident without try-hard energy. No overexplaining. No double-text energy.\n"
        "- Direct: one clean statement, not a paragraph of reasoning.\n"
        "- Cautious: shows interest without chasing. 'no rush' energy, not 'sorry to bother you'.\n"
        "- Never: multiple exclamation marks, compliment-stacking, validation-seeking.\n"
        "- Never escalate beyond demonstrated interest. Prioritize reciprocity over effort.\n\n"
        "When user is texting a MAN:\n"
        "- Playful: can be more overt — men respond to direct flirtation.\n"
        "- Direct: shorter and more blunt. Men parse intent from fewer words.\n"
        "- Cautious: warm but with screening quality, not withdrawal.\n"
        "- Never: passive-aggressive phrasing, hint-dropping that expects him to decode it.\n\n"
        "When gender is UNKNOWN:\n"
        "- Default to gender-neutral. Lean toward direct tone as safest cross-gender default.\n\n"
        "BANNED — NEVER GENERATE:\n"
        "- Deception tactics ('lie about', 'pretend that', 'tell them you')\n"
        "- Coercive language ('you owe me', 'after everything I did')\n"
        "- Guilt mechanisms ('if you really cared', 'I guess I'm not worth it')\n"
        "- Identity misrepresentation\n"
        "- Stalking facilitation\n\n"
        "Return ONLY a JSON array with exactly 3 objects. No preamble. No markdown.\n"
        "Each object: {\"tone\": \"playful\"|\"direct\"|\"cautious\", \"text\": \"...\"}"
    )


# Next-move advice that asks the user to lower the temperature. When it matches, replies
# switch to calm tones and the prompt bans teasing (2026-11-05 live read: the advice said
# "Stop escalating" while the Playful reply was a jab).
_DEESCALATE = _re.compile(
    r"\b(stop|quit) (escalating|pushing|arguing|chasing|pressing|debating)\b"
    r"|\b(drop|let go of|leave) (it|this|the topic|the subject)\b"
    r"|\blet it (go|rest|drop)\b|\bback off\b|\bstep back\b|\bcool (it|off|down)\b"
    r"|\bdon'?t (push|escalate|argue|press|chase)\b|\bstop trying to (convince|win)\b"
    r"|\bgive (them|her|him|it) (some )?space\b|\bless intensity\b|\blower the temperature\b",
    _re.I,
)


def needs_deescalation(next_move: str) -> bool:
    return bool(_DEESCALATE.search(next_move or ""))


_SCRAMBLED = ("out of order", "reverse", "scrambled", "chopped up", "chunks", "timeline is fuzzy")


def _last_lines(extracted_text: str) -> Dict[str, str]:
    out = {"them": "", "you": ""}
    for line in reversed([l.strip() for l in (extracted_text or "").splitlines() if l.strip()]):
        u = line.upper()
        if u.startswith("THEM:") and not out["them"]:
            out["them"] = line[5:].strip()
        elif u.startswith("YOU:") and not out["you"]:
            out["you"] = line[4:].strip()
        if out["them"] and out["you"]:
            break
    return out


def _build_user_prompt(
    extracted_text: str,
    reply_mode: str,
    other_gender: str,
    payload: dict,
) -> str:
    last = _last_lines(extracted_text)
    next_move = str(payload.get("practical_next_steps") or "").strip()
    read = str(payload.get("diagnosis") or "").strip()
    if reply_mode == "reply":
        mode_instruction = (
            f'Reply to THEIR last message: "{last["them"]}". You are writing as the user (YOU:), '
            "answering them. Never restate their words back as if the user had said them."
        )
    else:
        mode_instruction = (
            "Suggest what the user should send next (the user sent the last message, or the order is unclear). "
            "Do not reply to an older line as if it were the latest."
        )
    return (
        f"CONVERSATION:\n{extracted_text}\n\n"
        f"REPLY MODE: {reply_mode} — {mode_instruction}\n"
        f"OTHER PERSON'S GENDER: {other_gender}\n"
        f"ANALYSIS CONTEXT:\n"
        f"- Primary label: {payload.get('primary_label', 'unknown')}\n"
        f"- Social tone: {payload.get('social_tone', 'unknown')}\n"
        f"- Lane: {payload.get('lane', 'BENIGN')}\n"
        f"- Presentation mode: {payload.get('presentation_mode', 'risk')}\n"
        f"- The read: {read[:600]}\n"
        f"- YOUR NEXT MOVE (the advice shown to the user): {next_move[:800]}\n\n"
        "ALIGNMENT RULES:\n"
        "- Every suggestion must follow YOUR NEXT MOVE. If it says to drop a topic, no suggestion mentions it.\n"
        "- If it says to stop pitching or inviting, no suggestion pitches or invites.\n"
        "- If it recommends one specific ask, at most one suggestion makes that ask.\n"
        "- Speak as the user. Never answer a question the user asked; never repeat the other person's line as the user's own.\n"
        + (
            "- DE-ESCALATE: YOUR NEXT MOVE asks the user to lower the temperature. No teasing, sarcasm, digs, "
            "jokes at their expense, or points scored. Every suggestion is calmer than the user's last message. "
            "Use tones \"warm\", \"direct\", \"cautious\" only (no \"playful\").\n"
            if needs_deescalation(next_move) else ""
        )
        + "\nGenerate 3 reply suggestions. Raw JSON array only."
    )


def _validate_suggestions(suggestions: list, deescalate: bool = False) -> List[dict]:
    """Enforce constraints on LLM output. Drop malformed items.
    deescalate=True: a 'playful' item is dropped (the advice said to lower the temperature)."""
    valid = []
    allowed_tones = {"playful", "direct", "cautious", "warm"}
    for item in suggestions:
        if not isinstance(item, dict):
            continue
        tone = str(item.get("tone", "")).lower()
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        if tone not in allowed_tones:
            tone = "direct"
        if deescalate and tone == "playful":
            continue
        # Enforce 25-word cap — truncate if over
        words = text.split()
        if len(words) > 25:
            text = " ".join(words[:25])
        # Strip formal punctuation artifacts
        text = text.replace(";", "").replace(":", " ").replace("—", " ").replace("--", " ")
        # Strip trailing period on short messages (under 8 words)
        if len(words) <= 8 and text.endswith("."):
            text = text[:-1]
        valid.append({"tone": tone, "text": text.strip()})
    return valid[:3]


def _call_llm(
    extracted_text: str,
    reply_mode: str,
    other_gender: str,
    payload: dict,
) -> List[dict]:
    """Call Claude Haiku to generate reply suggestions. Returns parsed list or []."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.warning("reply_engine: no ANTHROPIC_API_KEY, skipping")
        return []

    try:
        import anthropic
        from app.llm_util import sdk_kwargs, sdk_text
        client = anthropic.Anthropic(api_key=api_key)
        _model = (os.environ.get("VL_REPLY_MODEL") or os.environ.get("VL_ENRICH_MODEL")
                  or "claude-haiku-4-5-20251001")
        message = client.messages.create(
            model=_model,
            max_tokens=1024,
            system=_build_system_prompt(),
            messages=[{
                "role": "user",
                "content": _build_user_prompt(extracted_text, reply_mode, other_gender, payload),
            }],
            **sdk_kwargs(_model),
        )
        raw = sdk_text(message).strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]
        if raw.endswith("```"):
            raw = raw.rsplit("```", 1)[0]
        raw = raw.strip()

        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            logger.warning("reply_engine: LLM returned non-array JSON")
            return []
        return _validate_suggestions(
            parsed, deescalate=needs_deescalation(str(payload.get("practical_next_steps") or "")))

    except json.JSONDecodeError as e:
        logger.warning(f"reply_engine: JSON parse failed: {e}")
        return []
    except Exception as e:
        logger.warning(f"reply_engine: LLM call failed: {e}")
        return []


def generate_replies(
    payload: dict,
    extracted_text: str,
    other_gender: str = "unknown",
) -> dict:
    """
    Generate 2-3 suggested replies based on conversation analysis.
    Lane-gated: FRAUD/COERCION_RISK suppress normal suggestions.
    Fail-closed: on any error, returns empty list — never blocks the read.

    Returns dict with keys:
        suggested_replies        : list of {tone, text}
        reply_mode               : 'reply' | 'next_move' | 'safety' | 'error'
        replies_suppressed       : bool
        replies_suppressed_reason: str | None
    """
    # Gate check
    if _is_gated(payload):
        return _safety_reply(payload)

    # Determine reply vs next-move
    reply_mode = _detect_reply_mode(extracted_text)
    # If the read says the screenshots are out of order, the last line is not the
    # latest message: suggest a next move instead of replying to an older line.
    _diag = (str(payload.get("diagnosis") or "") + " " + str(payload.get("reasoning") or "")).lower()
    if reply_mode == "reply" and any(k in _diag for k in _SCRAMBLED):
        reply_mode = "next_move"

    # Generate via LLM
    suggestions = _call_llm(extracted_text, reply_mode, other_gender, payload)

    if not suggestions:
        return {
            "suggested_replies": [],
            "reply_mode": "error",
            "replies_suppressed": False,
            "replies_suppressed_reason": "Reply generation unavailable",
        }

    return {
        "suggested_replies": suggestions,
        "reply_mode": reply_mode,
        "replies_suppressed": False,
        "replies_suppressed_reason": None,
    }
