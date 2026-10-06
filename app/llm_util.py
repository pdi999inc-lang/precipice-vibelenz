"""
llm_util.py - model-agnostic helpers for Anthropic calls.
Copyright © 2026 Ricky Sessums. All rights reserved.

Why this exists: newer models (Claude Sonnet 5.5) think by default and return a
thinking block before the text block, so `content[0].text` breaks. These helpers
(1) read only text blocks, and (2) turn off up-front thinking where the model
supports it, keeping latency and token use close to Haiku.

Pure, no network. Unknown models get no extra params (safe default).
"""
from __future__ import annotations

from typing import Any, Dict

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
