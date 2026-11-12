"""Copyright © 2026 Ricky Sessums. All rights reserved.

app/security_headers.py - baseline browser security headers on every response.

WHY
    A live check on 2026-11-11 found the site sending none of the standard
    browser security headers. These tell the browser to refuse a downgrade to
    plain HTTP, refuse to guess content types, and refuse to draw the site
    inside someone else's frame (clickjacking).

MVP SCOPE
    Five fixed headers, added to every response that does not already set them:
      Strict-Transport-Security  HTTPS only for one year (this host only)
      X-Content-Type-Options     no MIME sniffing
      X-Frame-Options            never framed
      Referrer-Policy            origin only on cross-site navigation
      Content-Security-Policy    frame-ancestors / base-uri / object-src ONLY
    The Content-Security-Policy is deliberately minimal. It does NOT restrict
    script, style, image, font or connect sources: the pages rely on inline
    scripts, Google Analytics and Google Fonts, and a script policy needs a pass
    in a real browser before it can be enforced. That is a later, separate step.

DAG PLACEMENT
    Outermost HTTP middleware. Not part of the analysis DAG: it never reads or
    changes a request body, a payload, a score, a lane or any copy.

DEGRADED BEHAVIOR / FAIL MODE
    Fails OPEN. Any internal error is logged and the response goes out without
    the headers. app/main.py imports this module inside a guard, so an import
    failure leaves the app serving.

KILL SWITCH
    VL_SECURITY_HEADERS_ENABLED=false removes the headers without a code change.
    Read per response. Note: a browser that has already seen the
    Strict-Transport-Security header keeps honouring it until max-age runs out.

PERFORMANCE
    One environment read and at most five header writes per response.

AUDIT LOGGING
    None. No decision is made here.

TESTS
    tests/test_security_headers.py
"""
from __future__ import annotations

import logging
import os
from typing import Dict

from fastapi import Request

log = logging.getLogger("vibelenz.security_headers")

HEADERS: Dict[str, str] = {
    "Strict-Transport-Security": "max-age=31536000",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Content-Security-Policy": "frame-ancestors 'none'; base-uri 'self'; object-src 'none'",
}


def enabled() -> bool:
    return os.environ.get("VL_SECURITY_HEADERS_ENABLED", "true").strip().lower() not in (
        "0", "false", "no", "off",
    )


async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    try:
        if enabled():
            for name, value in HEADERS.items():
                # Never overwrite a header a route chose to set itself.
                if name not in response.headers:
                    response.headers[name] = value
    except Exception as e:  # noqa: BLE001
        log.warning(f"security headers skipped: {type(e).__name__}")
    return response
