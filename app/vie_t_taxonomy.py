"""
app/vie_t_taxonomy.py - maps the LIVE signal vocabulary into VIE-T categories.

Copyright © 2026 Ricky Sessums. All rights reserved.

WHY THIS FILE EXISTS
--------------------
The August 2026 VIE-T prototype was built against a canonical SIG_* registry
that was never landed in this repo. The live analyzer emits snake_case IDs
(money_request, pressure_present, boundary_language_present, ...). A taxonomy
that does not match what the analyzer actually emits makes every
signal-dependent metric return zero, silently. This file exists so that cannot
happen quietly.

HOW DRIFT IS CAUGHT
-------------------
1. Categories are DERIVED from the live constants in app/analyzer_combined.py
   (grooming set, scam set, financial-concern set, CRITICAL tier of the live
   registry) plus a small explicit list. A signal added to those constants
   without a category is caught by tests/test_vie_t.py, which asserts every
   member is either categorized or listed in KNOWN_UNCATEGORIZED.
2. reconcile() compares any set of emitted IDs against this mapping. The shadow
   runner records the unmapped IDs of every request in the audit record, so
   drift shows up in data instead of in wrong metrics. It does NOT refuse to
   serve: VIE-T is shadow-mode and advisory, and an advisory layer must never
   take the request path down.

THE MAPPING IS JUDGMENT, NOT MEASUREMENT. Which category a signal belongs to is
a design decision that has not been validated against labeled conversations.
Do not present any VIE-T metric as calibrated.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Iterable, List

from app.analyzer_combined import (
    LOW_SEVERITY_RAPID_INTIMACY,
    RECIPROCAL_DAMPENERS,
    ROMANCE_HARD_GROOMING_SIGNALS,
    ROMANCE_SCAM_HARD_SIGNALS,
    SIGNAL_REGISTRY,
    _FINANCIAL_CONCERN_SIGNALS,
)
from app.vie_t import Taxonomy

TAXONOMY_VERSION = "vie-t-tax-2026-11-01-a"  # +hard_rejection_present, repair_attempt_present, warm_reception_present (context, unscored)

# Pressure applied TO the subject. Excludes the subject's own boundary assertion,
# which is the semantic opposite (v0.1.0 of the prototype conflated them).
_PRESSURE_EXPLICIT = {
    "pressure_present",
    "emergency_narrative",
    "blame_inversion",
    "concern_minimized",
    "secrecy_request",
}

_FINANCIAL_EXPLICIT = {
    "money_request",
    "financial_ask_escalation",
    "gift_lure_fee_extraction",
    "resource_extraction",
    "payment_before_verification",
}

_EXTRACTION_EXPLICIT = {
    "credential_or_sensitive_info_signal",
    "off_platform_migration",
    "platform_migration_push",
}

_CONTRADICTION_EXPLICIT = {
    "identity_inconsistency",
    "repeated_contradiction",
    "owner_identity_shift",
    "property_identity_shift",
    "verification_path_shift",
    "withheld_owner_verification",
    "verification_refusal",
    "verification_dodging",
}

# The subject asserting a limit. Not itself risk; the precondition that makes a
# later push meaningful.
_BOUNDARY_EXPRESSION = {"boundary_language_present"}

# Dampeners that mean pressure is absent.
_PRESSURE_RELIEF = {"no_pressure", "no_coercion"}

PRESSURE: FrozenSet[str] = frozenset(
    _PRESSURE_EXPLICIT | set(ROMANCE_HARD_GROOMING_SIGNALS)
)
FINANCIAL: FrozenSet[str] = frozenset(
    _FINANCIAL_EXPLICIT | set(_FINANCIAL_CONCERN_SIGNALS)
)
EXTRACTION: FrozenSet[str] = frozenset(_EXTRACTION_EXPLICIT)
CONTRADICTION: FrozenSet[str] = frozenset(_CONTRADICTION_EXPLICIT)
BOUNDARY_EXPRESSION: FrozenSet[str] = frozenset(_BOUNDARY_EXPRESSION)
PRESSURE_RELIEF: FrozenSet[str] = frozenset(_PRESSURE_RELIEF)
SAFETY_CRITICAL: FrozenSet[str] = frozenset(
    sid for sid, meta in SIGNAL_REGISTRY.items() if meta.get("tier") == "CRITICAL"
)

# Deliberately NOT part of any metric. Listed so they are visible as a decision
# rather than looking like an omission. Rapid-intimacy and positive/dampener
# signals are context, not harm; scoring them as harm moralizes consensual adult
# behavior and manufactures false positives.
KNOWN_UNCATEGORIZED: FrozenSet[str] = frozenset(
    (
        set(LOW_SEVERITY_RAPID_INTIMACY)
        # no_coercion is a reciprocity dampener AND a pressure-relief signal; it is
        # categorized as relief, so it must not also be listed as uncategorized.
        | (set(RECIPROCAL_DAMPENERS) - _PRESSURE_RELIEF)
        | {
        # live analyzer context / positives / dampeners
        "sexual_directness",
        "future_faking",
        "victim_state",
        "user_rationalizing",
        "reciprocal_engagement",
        "high_intent_present",
        "initial_confusion_present",
        "playful_engagement_present",
        "sexual_reciprocity_present",
        "no_extraction",
        "high_reciprocity",
        "short_term_alignment_noncoercive",
        "routine_transactional_context",
        # relational context surfaced by the 2026-11-01 eval corpus: not harm
        "hard_rejection_present",
        "repair_attempt_present",
        "warm_reception_present",
        # safety read handled by the physical-harm override, not by trajectory
        "physical_harm_disclosed",
        }
    )
)

LIVE_TAXONOMY = Taxonomy(
    pressure=PRESSURE,
    boundary_expression=BOUNDARY_EXPRESSION,
    financial=FINANCIAL,
    extraction=EXTRACTION,
    contradiction=CONTRADICTION,
    safety_critical=SAFETY_CRITICAL,
    pressure_relief=PRESSURE_RELIEF,
)

CATEGORIZED: FrozenSet[str] = frozenset(
    PRESSURE | FINANCIAL | EXTRACTION | CONTRADICTION | BOUNDARY_EXPRESSION
    | PRESSURE_RELIEF | SAFETY_CRITICAL
)
MAPPED: FrozenSet[str] = CATEGORIZED | KNOWN_UNCATEGORIZED

# Live constants whose every member must be categorized or explicitly listed.
# tests/test_vie_t.py asserts this, which is what makes drift structural.
LIVE_CONSTANTS: Dict[str, FrozenSet[str]] = {
    "ROMANCE_HARD_GROOMING_SIGNALS": frozenset(ROMANCE_HARD_GROOMING_SIGNALS),
    "ROMANCE_SCAM_HARD_SIGNALS": frozenset(ROMANCE_SCAM_HARD_SIGNALS),
    "LOW_SEVERITY_RAPID_INTIMACY": frozenset(LOW_SEVERITY_RAPID_INTIMACY),
    "RECIPROCAL_DAMPENERS": frozenset(RECIPROCAL_DAMPENERS),
    "FINANCIAL_CONCERN_SIGNALS": frozenset(_FINANCIAL_CONCERN_SIGNALS),
    "SIGNAL_REGISTRY": frozenset(SIGNAL_REGISTRY),
}

_SENTINELS = frozenset({"No signals detected", ""})


def reconcile(emitted: Iterable[str]) -> Dict[str, List[str]]:
    """
    Compare emitted signal IDs against this mapping.

    Returns {"unmapped": [...]}. "unmapped" is the dangerous list: signals that
    fire in production while VIE-T cannot see them. Callers record it; tests
    assert it is empty over the eval corpus.
    """
    ids = {str(s) for s in emitted if s is not None} - _SENTINELS
    return {"unmapped": sorted(ids - MAPPED)}
