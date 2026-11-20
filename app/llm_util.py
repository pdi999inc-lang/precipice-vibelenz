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

import contextvars
import logging
import os
import time
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


# ---------------------------------------------------------------------------
# Call instrumentation (WP2b). One row per AI call, keyed by request_id.
# NO conversation content is ever logged: only call site, model, token counts, latency, status.
# Logging is observability, not a safety gate: it is best-effort and never raises into an analysis.
# ---------------------------------------------------------------------------
_LOG = logging.getLogger(__name__)
_CTX: "contextvars.ContextVar[Dict[str, Any] | None]" = contextvars.ContextVar("vl_llm_ctx", default=None)


def set_llm_context(request_id: str, path: str | None = None, n_images: int | None = None) -> None:
    """Mark the current request as one whose AI calls are logged. Stores a mutable dict, so worker
    threads that received a copy of the context (Starlette run_in_threadpool) share the same dict.
    Outside a request (evals, the health probe, scripts) no context is set and nothing is logged."""
    _CTX.set({"request_id": str(request_id)[:64], "path": path, "n_images": n_images, "lane": None})


def current_llm_context() -> Dict[str, Any] | None:
    return _CTX.get()


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _write_row(call_site: str, model: Any, input_tokens: int | None, output_tokens: int | None,
               latency_ms: int, status: str) -> None:
    ctx = _CTX.get()
    if not ctx:
        return
    try:
        from app.db import log_llm_call
        log_llm_call(request_id=ctx["request_id"], call_site=call_site, model=model,
                     input_tokens=input_tokens, output_tokens=output_tokens, latency_ms=latency_ms,
                     status=status, lane=ctx.get("lane"), path=ctx.get("path"),
                     n_images=ctx.get("n_images"))
    except Exception as exc:  # noqa: BLE001 - logging must never break an analysis
        _LOG.warning("llm_call_log write skipped: %s", type(exc).__name__)


def logged_create(client: Any, call_site: str, **kwargs: Any) -> Any:
    """client.messages.create(**kwargs) plus one log row. Same arguments, same result, same
    exceptions as the bare call; the row is written whether the call succeeds or fails."""
    start = time.monotonic()
    message = None
    status = "ok"
    try:
        message = client.messages.create(**kwargs)
        return message
    except Exception:
        status = "error"
        raise
    finally:
        try:
            usage = getattr(message, "usage", None)
            _write_row(call_site, kwargs.get("model"),
                       _as_int(getattr(usage, "input_tokens", None)),
                       _as_int(getattr(usage, "output_tokens", None)),
                       int((time.monotonic() - start) * 1000), status)
        except Exception:  # noqa: BLE001
            pass


def record_raw_call(call_site: str, model: Any, data: Any, latency_ms: int, status: str = "ok") -> None:
    """Same row for the raw-HTTP vision OCR call, which does not go through the SDK."""
    try:
        usage = data.get("usage") if isinstance(data, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        _write_row(call_site, model, _as_int(usage.get("input_tokens")),
                   _as_int(usage.get("output_tokens")), int(latency_ms), status)
    except Exception:  # noqa: BLE001
        pass


def finalize_llm_context(lane: Any) -> None:
    """Backfill the lane on this request's rows once it is known (it is not known at call time)."""
    ctx = _CTX.get()
    if not ctx or not lane:
        return
    ctx["lane"] = str(lane)[:40]
    try:
        from app.db import set_llm_call_lane
        set_llm_call_lane(ctx["request_id"], ctx["lane"])
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("llm_call_log lane backfill skipped: %s", type(exc).__name__)

