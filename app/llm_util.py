"""
llm_util.py - model-agnostic helpers for Anthropic calls.
Copyright © 2026 Ricky Sessums. All rights reserved.

Why this exists: newer models (Claude Sonnet 5.5) think by default and return a
thinking block before the text block, so `content[0].text` breaks. These helpers
(1) read only text blocks, and (2) turn off up-front thinking where the model
supports it, keeping latency and token use close to Haiku.

Pure, no network. Unknown models get no extra params (safe default).

client_kwargs() (2026-11-11) caps how long one AI call may run. Without it the SDK
default applies (10 minutes per attempt, 2 retries), so a stalled upstream pins a
worker thread and leaves the user waiting. On timeout the callers' existing error
paths run: analysis falls back to the backup engine (marked degraded), narrative and
replies fall back to their deterministic copy, follow-up returns 'unavailable'.
"""
from __future__ import annotations

import os
from typing import Any, Dict

# Defaults. One live read observed on 2026-11-05 took about 51s end to end across
# several sequential calls, so 90s per call leaves wide headroom. The margin is on
# purpose: a timeout set too low would quietly push slow-but-healthy reads onto the
# backup engine. Tune with a Railway variable, no code change:
#   VL_LLM_TIMEOUT_S     seconds per attempt (5-600)
#   VL_LLM_MAX_RETRIES   SDK retries after the first attempt (0-5)
DEFAULT_LLM_TIMEOUT_S = 90.0
DEFAULT_LLM_MAX_RETRIES = 1


def _env_number(name: str, default: float, lo: float, hi: float) -> float:
    try:
        value = float(str(os.environ.get(name, "")).strip())
    except (TypeError, ValueError):
        return default
    if value != value:  # NaN
        return default
    return max(lo, min(hi, value))


def client_kwargs() -> Dict[str, Any]:
    """Timeout and retry cap for anthropic.Anthropic(...). Bad or missing env values
    fall back to the defaults; values are clamped to a sane range."""
    return {
        "timeout": _env_number("VL_LLM_TIMEOUT_S", DEFAULT_LLM_TIMEOUT_S, 5.0, 600.0),
        "max_retries": int(_env_number("VL_LLM_MAX_RETRIES", DEFAULT_LLM_MAX_RETRIES, 0, 5)),
    }

# Models whose default thinking must be switched off explicitly.
# Sonnet 5.5 rejects {"type": "disabled"} (400); "between_tools" is its lowest
# setting and, without tools, returns text only. Haiku 4.5 needs nothing.
_NO_UPFRONT_THINKING = {
    "claude-sonnet-5-5": {"type": "between_tools"},
}


def thinking_config(model: str) -> Dict[str, Any] | None:
    return _NO_UPFRONT_THINKING.get(str(model or "").strip())


def sdk_kwargs(model: str) -> Dict[str, Any]:
    """Extra kwargs for client.messages.create(). Uses extra_body so the pinned
    SDK version never rejects an unfamiliar thinking type client-side."""
    cfg = thinking_config(model)
    return {"extra_body": {"thinking": cfg}} if cfg else {}


def payload_extras(model: str) -> Dict[str, Any]:
    """Extra keys for a raw /v1/messages JSON payload."""
    cfg = thinking_config(model)
    return {"thinking": cfg} if cfg else {}


def sdk_text(message: Any) -> str:
    """Concatenate text blocks from an SDK Message; ignore thinking blocks."""
    parts = []
    for block in getattr(message, "content", None) or []:
        # Real API blocks carry type="text"; tolerate a missing type (older SDKs, mocks).
        if getattr(block, "type", "text") == "text" and getattr(block, "text", None):
            parts.append(getattr(block, "text", "") or "")
    text = "".join(parts).strip()
    if not text:
        raise ValueError("model returned no text block")
    return text


def json_text(data: Dict[str, Any]) -> str:
    """Concatenate text blocks from a raw /v1/messages JSON response."""
    parts = [b.get("text", "") or "" for b in (data.get("content") or [])
             if isinstance(b, dict) and b.get("type", "text") == "text" and b.get("text")]
    text = "".join(parts).strip()
    if not text:
        raise ValueError("model returned no text block")
    return text
