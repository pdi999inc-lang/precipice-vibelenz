"""
llm_health.py — is the AI path actually serving reads? Backs GET /health/llm.

Copyright (c) 2026 Ricky Sessums. All rights reserved.

WHY (2026-11-01)
  The Anthropic account ran out of credit. Every read silently fell back to the
  deterministic engine, and nothing told anyone. /health only says the web
  process is up.

MVP SCOPE
  - Passive: every finished read is recorded (degraded or not, plus a coarse
    reason class). No conversation content, no request ids.
  - Active: when the endpoint is hit, at most once every PROBE_INTERVAL_S, a
    1-token call checks the key/credit. Bounded cost (~96 tiny calls/day if an
    uptime monitor polls every 5 min) and works with zero user traffic.
  - Status: "billing" (credit/auth failure, from a probe or a read),
    "degraded" (>= DEGRADED_MIN reads in the window and >= DEGRADED_RATE fell
    back), else "ok". Endpoint returns 503 for anything but "ok", so a free
    uptime monitor (keyword or status check) can email the owner.

DEGRADED BEHAVIOR
  Never raises into the request path. Probe errors become a status, not a crash.
  VL_LLM_PROBE=off disables the active probe (passive tracking still runs).

AUDIT / LOGGING
  One ERROR log line "[ALERT] llm_health ..." on each transition into a bad
  state, one INFO line on recovery. No content logged.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

WINDOW_S = 30 * 60
DEGRADED_MIN = 3
DEGRADED_RATE = 0.5
PROBE_INTERVAL_S = 15 * 60
_MAXLEN = 200

_BILLING = re.compile(r"credit\s+balance|billing|insufficient|payment|authentication|invalid\s+x-api-key|api[_\s-]?key|401|403", re.I)

_lock = threading.Lock()
_reads: Deque[Tuple[float, bool, str]] = deque(maxlen=_MAXLEN)
_probe: Dict[str, Any] = {"at": 0.0, "ok": None, "kind": None}
_last_status = "ok"


def classify(reason: Optional[str]) -> str:
    if not reason:
        return "none"
    return "billing" if _BILLING.search(str(reason)) else "other"


def record_read(degraded: bool, reason: Optional[str] = None, now: Optional[float] = None) -> None:
    try:
        with _lock:
            _reads.append((now if now is not None else time.time(), bool(degraded), classify(reason) if degraded else "none"))
    except Exception:  # noqa: BLE001
        pass


def _default_probe() -> Tuple[bool, str]:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return False, "billing"
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key, timeout=15.0, max_retries=0)
        client.messages.create(
            model=(os.environ.get("VL_PROBE_MODEL") or os.environ.get("VL_ANALYSIS_MODEL") or "claude-haiku-5-5"),
            max_tokens=1,
            messages=[{"role": "user", "content": "ok"}],
        )
        return True, "none"
    except Exception as e:  # noqa: BLE001
        return False, classify(f"{type(e).__name__} {e}")


def _maybe_probe(now: float, prober) -> None:
    if os.environ.get("VL_LLM_PROBE", "on").lower() == "off":
        return
    with _lock:
        if now - _probe["at"] < PROBE_INTERVAL_S:
            return
        _probe["at"] = now  # reserve the slot before the network call
    ok, kind = prober()
    with _lock:
        _probe["ok"], _probe["kind"] = ok, kind


def status(now: Optional[float] = None, prober=None) -> Dict[str, Any]:
    global _last_status
    now = now if now is not None else time.time()
    try:
        _maybe_probe(now, prober or _default_probe)
    except Exception:  # noqa: BLE001
        pass
    with _lock:
        recent = [r for r in _reads if now - r[0] <= WINDOW_S]
        probe_ok, probe_kind = _probe["ok"], _probe["kind"]
    n = len(recent)
    n_deg = sum(1 for r in recent if r[1])
    billing_reads = any(r[2] == "billing" for r in recent[-5:])
    if probe_ok is False and probe_kind == "billing" or billing_reads:
        st = "billing"
    elif (n >= DEGRADED_MIN and n_deg / n >= DEGRADED_RATE) or probe_ok is False:
        st = "degraded"
    else:
        st = "ok"
    if st != _last_status:
        if st == "ok":
            logger.info(f"[llm_health] recovered (was {_last_status})")
        else:
            logger.error(f"[ALERT] llm_health status={st} reads_30m={n} degraded_30m={n_deg} probe_ok={probe_ok}")
        _last_status = st
    return {"status": st, "reads_30m": n, "degraded_30m": n_deg,
            "probe_ok": probe_ok, "probe_age_s": int(now - _probe["at"]) if _probe["at"] else None}


def _reset_for_tests() -> None:
    global _last_status
    with _lock:
        _reads.clear()
        _probe.update({"at": 0.0, "ok": None, "kind": None})
    _last_status = "ok"
