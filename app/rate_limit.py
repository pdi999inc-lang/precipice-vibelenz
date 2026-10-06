"""Copyright © 2026 Ricky Sessums. All rights reserved.

app/rate_limit.py - per-client request limits for VibeLenz POST endpoints.

WHY
    Every analysis and follow-up spends Anthropic credit, and the email capture
    sends mail through SendGrid. Without limits one script can drain the
    Anthropic spend limit (pushing every real user onto the degraded backup
    engine) or email-bomb a stranger. This middleware caps how often a single
    client can hit those endpoints.

HOW
    Sliding-window counters in memory, keyed by (bucket, client IP). Railway
    runs one uvicorn process, so in-memory state is shared by every request.
    It resets on redeploy, which is acceptable for abuse control.

CLIENT IP
    Railway's edge proxy appends the connecting address to X-Forwarded-For, so
    values a client sends itself sit to the LEFT of Railway's entry. We take the
    rightmost public address (skipping private/internal hops), then fall back to
    X-Real-IP, then the socket peer. A client therefore cannot pick its own key
    by forging headers.

FAIL MODE
    Fails OPEN on any internal error (logged): a bug in the limiter must never
    take the app down. Blocked requests return 429 JSON with a plain-language
    message and a Retry-After header; they never reach the email gate, so a
    blocked analysis does not count as a use.

KILL SWITCH
    VL_RATE_LIMITS_ENABLED=false disables all limits without a code change.
"""
from __future__ import annotations

import ipaddress
import logging
import math
import os
import threading
import time
from collections import deque
from typing import Deque, Dict, List, Optional, Tuple

from fastapi import Request
from fastapi.responses import JSONResponse

log = logging.getLogger("vibelenz.rate_limit")

# (limit, window_seconds) pairs per bucket. Every rule in a bucket must pass.
RULES: Dict[str, List[Tuple[int, int]]] = {
    # Paid: vision OCR + analysis + enrichment per call.
    "analyze": [(8, 600), (30, 86400)],
    # Paid: one AI call per question; per-read cap of 5 is enforced separately.
    "followup": [(20, 600), (60, 86400)],
    # Sends email through SendGrid.
    "email": [(5, 600), (20, 86400)],
    # Cheap DB writes; limited only to stop log/label flooding.
    "light": [(30, 600)],
}

# POST path -> bucket. GET pages and /health are never limited.
PATH_BUCKETS: Dict[str, str] = {
    "/analyze-screenshots": "analyze",
    "/followup": "followup",
    "/email/capture": "email",
    "/email/unsubscribe": "email",
    "/literacy/answer": "light",
    "/feedback": "light",
    "/outcome": "light",
    "/log-session": "light",
}

MESSAGES: Dict[str, str] = {
    "analyze": "You've run a lot of reads in a short time. Give it a few minutes and try again.",
    "followup": "That's a lot of questions in a short time. Give it a few minutes and try again.",
    "email": "Too many tries. Give it a few minutes and try again.",
    "light": "Too many requests. Give it a few minutes and try again.",
}

_MAX_KEYS = 50000  # memory bound; oldest-idle keys are dropped past this

_lock = threading.Lock()
_hits: Dict[Tuple[str, str], Deque[float]] = {}


def enabled() -> bool:
    return os.environ.get("VL_RATE_LIMITS_ENABLED", "true").strip().lower() not in (
        "0", "false", "no", "off",
    )


def reset() -> None:
    """Clear all counters (tests, or an operator after a false positive)."""
    with _lock:
        _hits.clear()


def _is_public(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value.strip())
    except ValueError:
        return False
    return ip.is_global


def client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        for part in reversed([p.strip() for p in xff.split(",") if p.strip()]):
            if _is_public(part):
                return part
    real = request.headers.get("x-real-ip", "").strip()
    if real and _is_public(real):
        return real
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _mask(ip: str) -> str:
    """Log-safe form: drop the host part so logs never hold a full address."""
    if ":" in ip:
        return ":".join(ip.split(":")[:3]) + "::/48"
    parts = ip.split(".")
    if len(parts) == 4:
        return ".".join(parts[:3]) + ".0/24"
    return "unknown"


def check(bucket: str, key: str, now: Optional[float] = None) -> Optional[int]:
    """Record one hit. Returns None if allowed, else seconds until a slot frees.

    A blocked hit is not recorded, so hammering while blocked does not extend
    the block.
    """
    rules = RULES.get(bucket)
    if not rules:
        return None
    now = time.monotonic() if now is None else now
    longest = max(w for _, w in rules)
    with _lock:
        q = _hits.get((bucket, key))
        if q is None:
            if len(_hits) >= _MAX_KEYS:
                _evict(now)
            q = deque()
            _hits[(bucket, key)] = q
        while q and now - q[0] >= longest:
            q.popleft()
        wait = 0.0
        for limit, window in rules:
            in_window = [t for t in q if now - t < window]
            if limit <= 0:
                wait = max(wait, float(window))
            elif len(in_window) >= limit:
                wait = max(wait, window - (now - in_window[-limit]))
        if wait > 0:
            return max(1, int(math.ceil(wait)))
        q.append(now)
        return None


def _evict(now: float) -> None:
    # Called with _lock held. Drop idle keys first; if still full, drop half.
    stale = [k for k, q in _hits.items() if not q or now - q[-1] >= 86400]
    for k in stale:
        del _hits[k]
    if len(_hits) >= _MAX_KEYS:
        for k in list(_hits.keys())[: _MAX_KEYS // 2]:
            del _hits[k]


async def rate_limit_middleware(request: Request, call_next):
    bucket = PATH_BUCKETS.get(request.url.path) if request.method == "POST" else None
    if bucket and enabled():
        try:
            ip = client_ip(request)
            retry = check(bucket, ip)
        except Exception:
            log.exception("rate_limit decision=FAIL_OPEN path=%s", request.url.path)
            retry = None
        if retry is not None:
            log.warning(
                "rate_limit decision=BLOCK bucket=%s client=%s retry_after=%ss",
                bucket, _mask(ip), retry,
            )
            return JSONResponse(
                status_code=429,
                content={"error": "rate_limited", "message": MESSAGES[bucket]},
                headers={"Retry-After": str(retry)},
            )
    return await call_next(request)
