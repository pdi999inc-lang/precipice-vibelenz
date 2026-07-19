"""
loadtest_mode.py - staging-only latency simulation for capacity testing.

WHY THIS EXISTS
    In production, an analysis spends almost all its wall-clock time awaiting
    Anthropic (one vision OCR call per image + one analysis call + narrative
    enrichment). The container is I/O-bound, not CPU-bound. To measure how many
    concurrent analyses the container holds before latency/health degrade, we
    must reproduce that "hold a slot while awaiting Anthropic" behavior WITHOUT
    making real API calls (which cost money and burn rate limits).

HOW IT WORKS
    Activated only by env VIBELENZ_LOADTEST=1. When on, the analyze endpoint
    sleeps for a configurable duration that mimics the Anthropic round-trip.
    It does NOT alter any analysis, safety, or schema logic.

SAFE STAGING SETUP (a SEPARATE Railway service, never production):
    VIBELENZ_LOADTEST = 1
    ANTHROPIC_API_KEY : LEAVE UNSET  -> pipeline runs its deterministic
                                        fallback, zero API cost
    DATABASE_URL      : LEAVE UNSET  -> get_conn() returns None, every DB
                                        write is a safe no-op

    With both keys unset the app already runs end-to-end via its own fail-closed
    fallbacks; this module only adds the artificial latency so concurrency and
    memory behave like production.

PRODUCTION GUARD
    This flag must never be set in production. If it is, it only injects latency
    (it cannot leak data or bypass safety), but it is logged loudly at startup so
    the misconfiguration is obvious.
"""
import logging
import os

logger = logging.getLogger("vibelenz.loadtest")

ENABLED = os.environ.get("VIBELENZ_LOADTEST", "").strip().lower() in ("1", "true", "yes", "on")

# Simulated Anthropic latencies (ms). Defaults are rough real-world medians;
# override per run to explore sensitivity.
VISION_MS_PER_IMAGE = int(os.environ.get("VIBELENZ_LOADTEST_VISION_MS", "1500"))
ANALYSIS_MS = int(os.environ.get("VIBELENZ_LOADTEST_ANALYSIS_MS", "3500"))

if ENABLED:
    logger.warning(
        "VIBELENZ_LOADTEST ENABLED - simulating Anthropic latency "
        "(vision=%dms/img, analysis=%dms). This MUST NOT run in production.",
        VISION_MS_PER_IMAGE, ANALYSIS_MS,
    )


def simulated_latency_seconds(num_images: int) -> float:
    """Total simulated upstream wait for one analysis: per-image vision + one analysis pass."""
    n = max(1, int(num_images or 1))
    return (n * VISION_MS_PER_IMAGE + ANALYSIS_MS) / 1000.0
