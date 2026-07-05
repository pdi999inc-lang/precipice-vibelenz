from __future__ import annotations

import json
import logging
import os
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


def _build_user_prompt(
    extracted_text: str,
    reply_mode: str,
    other_gender: str,
    payload: dict,
) -> str:
    mode_instruction = (
        "Respond specifically to their last message (THEM:)."
        if reply_mode == "reply"
        else "Suggest what to send next — the user sent the last message or is re-initiating."
    )
    return (
        f"CONVERSATION:\n{extracted_text}\n\n"
        f"REPLY MODE: {reply_mode} — {mode_instruction}\n"
        f"OTHER PERSON'S GENDER: {other_gender}\n"
        f"ANALYSIS CONTEXT:\n"
        f"- Primary label: {payload.get('primary_label', 'unknown')}\n"
        f"- Social tone: {payload.get('social_tone', 'unknown')}\n"
        f"- Lane: {payload.get('lane', 'BENIGN')}\n"
        f"- Presentation mode: {payload.get('presentation_mode', 'risk')}\n\n"
        f"Generate 3 reply suggestions. Raw JSON array only."
    )


def _validate_suggestions(suggestions: list) -> List[dict]:
    """Enforce constraints on LLM output. Drop malformed items."""
    valid = []
    allowed_tones = {"playful", "direct", "cautious"}
    for item in suggestions:
        if not isinstance(item, dict):
            continue
        tone = str(item.get("tone", "")).lower()
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        if tone not in allowed_tones:
            tone = "direct"
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
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            system=_build_system_prompt(),
            messages=[{
                "role": "user",
                "content": _build_user_prompt(extracted_text, reply_mode, other_gender, payload),
            }],
        )
        raw = message.content[0].text.strip()
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
        return _validate_suggestions(parsed)

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
