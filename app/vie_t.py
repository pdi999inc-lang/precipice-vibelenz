"""
app/vie_t.py - VIE-T (Verified Interaction Trajectory) engine.
VIE-T v0.4.0 - Tier 1 only. Shadow mode: advisory, audit-only.

Copyright © 2026 Ricky Sessums. All rights reserved.

NAME COLLISION NOTE
-------------------
app/trajectory.py is the CROSS-SESSION "compared to last time" module and is
imported by app/main.py. This module is the IN-CONVERSATION trajectory engine.
They are different things. Do not merge them and do not rename either file
without a version tag.

WHAT THIS IS
------------
A deterministic layer that answers "what changed inside this conversation?"
rather than "what does this message mean?". It consumes per-message observations
and derives:
    state    8 bounded metrics describing how the interaction is moving
    deltas   recent window vs baseline window
    events   discrete named transitions, each anchored to evidence turn IDs
    sticky   turns that must never be dropped from an evidence window

WHAT THIS IS NOT (Tier 2/3, refused or deferred - see VIE_T_SPEC.md)
    sparse attention / kernels, MoE routing, learned masks or classifiers,
    trajectory forecasting.

STATUS: SHADOW MODE
-------------------
Output is written to the audit record only. It is NOT shown to users and it
does NOT change any risk score, lane, or action. It may never lower or clear a
risk decision. Promotion past shadow requires the A/B in VIE_T_SPEC.md section 7
to pass, with its kill criteria honored.

DESIGN CONSTRAINTS (project invariants)
---------------------------------------
1. DETERMINISTIC. Pure function. No LLM, RNG, clock, or I/O.
2. FAILS CLOSED. Thin data -> degraded result with a confidence CEILING and no
   events. Never raises into the request path.
3. BOUNDED. Every metric clamped to a documented range.
4. ADDITIVE. Emits its own object; mutates nothing.
5. STDLIB ONLY. No new dependencies. No imports from other app modules: the
   live signal vocabulary is injected via a Taxonomy (see app/vie_t_taxonomy.py).

Every threshold in TrajectoryConfig is an UNVALIDATED starting point. They are
versioned (CONFIG_VERSION) so tuning is a visible, auditable change.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Set

VIE_T_VERSION = "0.4.0"
CONFIG_VERSION = "vie-t-cfg-2026-10-21-a"


# --------------------------------------------------------------------------
# Taxonomy (injected)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Taxonomy:
    """
    Semantic categories over signal IDs. Supplied by the caller so this engine
    stays pure and so the live vocabulary has exactly one owner
    (app/vie_t_taxonomy.py).
    """

    pressure: FrozenSet[str] = frozenset()             # coercive pressure applied TO the subject
    boundary_expression: FrozenSet[str] = frozenset()  # the subject ASSERTING a limit (not pressure)
    financial: FrozenSet[str] = frozenset()
    extraction: FrozenSet[str] = frozenset()
    contradiction: FrozenSet[str] = frozenset()
    safety_critical: FrozenSet[str] = frozenset()
    pressure_relief: FrozenSet[str] = frozenset()      # dampeners meaning pressure is absent


EMPTY_TAXONOMY = Taxonomy()


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class TrajectoryConfig:
    """Frozen so a request cannot mutate thresholds mid-flight."""

    window: int = 6
    baseline: int = 6
    min_turns_for_state: int = 4
    min_turns_for_deltas: int = 8

    text_len_cap: int = 400
    risk_scale: float = 100.0

    accel_points: float = 10.0
    decel_points: float = -10.0
    escalation_boundary_delta: float = 0.10
    withdrawal_engagement_delta: float = -0.20
    withdrawal_reciprocity_delta: float = -0.15
    repair_reciprocity_delta: float = 0.15
    repair_risk_points: float = -5.0
    destabilization_volatility_delta: float = 0.25
    shift_delta: float = 0.20

    recent_k: int = 4
    high_signal_window: int = 40
    high_risk_threshold: float = 70.0
    force_keep_safety: bool = True

    ceiling_low_context: float = 0.50
    ceiling_no_baseline: float = 0.70
    ceiling_missing_timestamps: float = 0.90


DEFAULT_CONFIG = TrajectoryConfig()


# --------------------------------------------------------------------------
# Input / output contracts
# --------------------------------------------------------------------------

@dataclass
class TurnObservation:
    """One analyzed message. The adapter boundary between the live analyzer and VIE-T."""

    turn_id: str
    speaker: str
    text_len: int = 0
    question_count: int = 0
    risk_score: float = 0.0
    signals: List[str] = field(default_factory=list)
    safety_critical: bool = False
    timestamp: Optional[float] = None


@dataclass
class TrajectoryEvent:
    event: str
    magnitude: float
    turn_ids: List[str]
    detail: str


@dataclass
class TrajectoryResult:
    vie_t_version: str
    config_version: str

    reciprocity: float
    engagement_quality: float
    emotional_volatility: float
    conversational_drift: Optional[float]
    boundary_pressure: float
    consistency: float
    escalation_pressure: float
    risk_level: float

    risk_acceleration_points: Optional[float]
    risk_acceleration: Optional[float]
    reciprocity_delta: Optional[float]
    engagement_delta: Optional[float]
    volatility_delta: Optional[float]
    boundary_delta: Optional[float]

    events: List[TrajectoryEvent]
    sticky_turn_ids: List[str]

    confidence: float
    confidence_ceiling: float
    degraded: bool
    degradation_states: List[str]
    reason_codes: List[str]
    turns_observed: int

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["events"] = [asdict(e) for e in self.events]
        return d

    def to_audit_fields(self) -> Dict[str, Any]:
        """Compact, bounded projection for the audit record."""
        return {
            "vie_t_version": self.vie_t_version,
            "config_version": self.config_version,
            "trajectory_confidence": round(self.confidence, 3),
            "trajectory_degraded": self.degraded,
            "trajectory_degradation_states": self.degradation_states,
            "risk_acceleration_points": self.risk_acceleration_points,
            "reciprocity": round(self.reciprocity, 3),
            "engagement_quality": round(self.engagement_quality, 3),
            "emotional_volatility": round(self.emotional_volatility, 3),
            "boundary_pressure": round(self.boundary_pressure, 3),
            "trajectory_events": [e.event for e in self.events],
            "sticky_turn_count": len(self.sticky_turn_ids),
            "trajectory_reason_codes": self.reason_codes,
            "turns_observed": self.turns_observed,
        }


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    if x != x:  # NaN fails closed to the low bound
        return lo
    return lo if x < lo else hi if x > hi else x


def _balance(a: float, b: float) -> float:
    total = a + b
    if total <= 0:
        return 0.0
    return 1.0 - abs(a - b) / total


def _stdev(xs: Sequence[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mean = sum(xs) / n
    return math.sqrt(sum((x - mean) ** 2 for x in xs) / n)


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _signal_set(turns: Sequence[TurnObservation]) -> Set[str]:
    out: Set[str] = set()
    for t in turns:
        out.update(t.signals)
    return out


def _safe_risk(t: TurnObservation, cfg: TrajectoryConfig) -> float:
    """Risk score clamped to its documented domain; non-finite values fail to the cap."""
    r = t.risk_score
    if r != r:
        return 0.0
    return max(0.0, min(cfg.risk_scale, r))


# --------------------------------------------------------------------------
# Metrics - each a pure function of a turn window
# --------------------------------------------------------------------------

def _reciprocity(turns: Sequence[TurnObservation]) -> float:
    if not turns:
        return 0.0
    speakers = {t.speaker for t in turns}
    if len(speakers) < 2:
        return 0.0
    a, b = sorted(speakers)[:2]
    counts = (
        sum(1 for t in turns if t.speaker == a),
        sum(1 for t in turns if t.speaker == b),
    )
    lengths = (
        sum(max(0, t.text_len) for t in turns if t.speaker == a),
        sum(max(0, t.text_len) for t in turns if t.speaker == b),
    )
    questions = (
        sum(max(0, t.question_count) for t in turns if t.speaker == a),
        sum(max(0, t.question_count) for t in turns if t.speaker == b),
    )
    return _clamp(
        0.5 * _balance(*counts) + 0.3 * _balance(*lengths) + 0.2 * _balance(*questions)
    )


def _engagement_quality(turns: Sequence[TurnObservation], cfg: TrajectoryConfig) -> float:
    if not turns:
        return 0.0
    length_term = _mean([_clamp(max(0, t.text_len) / cfg.text_len_cap) for t in turns])
    question_term = _clamp(_mean([min(max(0, t.question_count), 2) / 2.0 for t in turns]))
    return _clamp(0.65 * length_term + 0.35 * question_term)


def _emotional_volatility(turns: Sequence[TurnObservation], cfg: TrajectoryConfig) -> float:
    """
    PROXY WARNING: risk_score stands in for emotional intensity because no
    valence/arousal field exists in the pipeline. "Risky" and "emotionally
    volatile" correlate but are not the same. Highest-value refinement once
    calibration data exists.
    """
    if len(turns) < 2:
        return 0.0
    risks = [_safe_risk(t, cfg) for t in turns]
    deltas = [risks[i] - risks[i - 1] for i in range(1, len(risks))]
    return _clamp(_stdev(deltas) / (cfg.risk_scale / 2.0))


def _conversational_drift(
    recent: Sequence[TurnObservation], baseline: Sequence[TurnObservation]
) -> Optional[float]:
    if not baseline:
        return None
    a, b = _signal_set(recent), _signal_set(baseline)
    if not a and not b:
        return 0.0
    return _clamp(1.0 - len(a & b) / len(a | b))


def _boundary_pressure(turns: Sequence[TurnObservation], tax: Taxonomy) -> float:
    """
    Pressure-signal density, amplified when pressure RECURS after pressure has
    already appeared. Repetition is the coercive pattern; a single instance is not.
    """
    if not turns:
        return 0.0
    score = 0.0
    seen = False
    for t in turns:
        sigs = set(t.signals)
        hits = len(sigs & tax.pressure)
        if hits:
            score += hits * (2.0 if seen else 1.0)
            seen = True
        if sigs & tax.pressure_relief:
            score = max(0.0, score - 1.0)
    return _clamp(score / (len(turns) * 1.5))


def _consistency(turns: Sequence[TurnObservation], tax: Taxonomy) -> float:
    if not turns:
        return 1.0
    hits = sum(1 for t in turns if set(t.signals) & tax.contradiction)
    return _clamp(1.0 - hits / len(turns))


def _escalation_pressure(turns: Sequence[TurnObservation], tax: Taxonomy) -> float:
    if not turns:
        return 0.0
    pressure = tax.pressure | tax.financial | tax.extraction
    hits = sum(len(set(t.signals) & pressure) for t in turns)
    return _clamp(hits / (len(turns) * 1.5))


# --------------------------------------------------------------------------
# Sticky turns
# --------------------------------------------------------------------------

def _sticky_turns(
    turns: Sequence[TurnObservation], cfg: TrajectoryConfig, tax: Taxonomy
) -> List[str]:
    """
    Turns that must remain available to any later evidence-window decision:
    recent tail; safety-critical (never decays); high-signal (within horizon).
    Rationale: a boundary push at T14 explains a financial ask at T17 even when
    T14 is not itself the most volatile turn.
    """
    n = len(turns)
    if n == 0:
        return []
    keep: Set[str] = set()
    notable = tax.pressure | tax.financial | tax.extraction | tax.contradiction | tax.boundary_expression

    for idx, t in enumerate(turns):
        age = n - 1 - idx
        if age < cfg.recent_k:
            keep.add(t.turn_id)
            continue
        is_safety = t.safety_critical or bool(set(t.signals) & tax.safety_critical)
        if is_safety and cfg.force_keep_safety:
            keep.add(t.turn_id)
            continue
        is_high_signal = _safe_risk(t, cfg) >= cfg.high_risk_threshold or bool(set(t.signals) & notable)
        if is_high_signal and age < cfg.high_signal_window:
            keep.add(t.turn_id)

    return [t.turn_id for t in turns if t.turn_id in keep]


# --------------------------------------------------------------------------
# Event detection
# --------------------------------------------------------------------------

def _detect_events(
    recent: Sequence[TurnObservation],
    baseline: Sequence[TurnObservation],
    d: Dict[str, float],
    cfg: TrajectoryConfig,
    tax: Taxonomy,
) -> List[TrajectoryEvent]:
    events: List[TrajectoryEvent] = []
    recent_ids = [t.turn_id for t in recent]

    def add(name: str, mag: float, detail: str, ids: Optional[List[str]] = None) -> None:
        events.append(
            TrajectoryEvent(
                event=name,
                magnitude=round(_clamp(abs(mag)), 3),
                turn_ids=ids if ids is not None else recent_ids,
                detail=detail,
            )
        )

    rap = d["risk_acceleration_points"]
    pressure_like = tax.pressure | tax.financial | tax.extraction

    if rap >= cfg.accel_points and d["boundary_delta"] >= cfg.escalation_boundary_delta:
        pressure_ids = [t.turn_id for t in recent if set(t.signals) & pressure_like]
        add(
            "ESCALATION",
            rap / cfg.risk_scale,
            f"Risk rose {rap:+.1f} points while pressure signals rose {d['boundary_delta']:+.2f}.",
            pressure_ids or recent_ids,
        )
    elif rap >= cfg.accel_points:
        add("ACCELERATION", rap / cfg.risk_scale, f"Risk rose {rap:+.1f} points.")
    elif rap <= cfg.decel_points:
        add("DECELERATION", rap / cfg.risk_scale, f"Risk fell {rap:+.1f} points.")

    if (
        d["engagement_delta"] <= cfg.withdrawal_engagement_delta
        and d["reciprocity_delta"] <= cfg.withdrawal_reciprocity_delta
    ):
        add(
            "WITHDRAWAL",
            max(abs(d["engagement_delta"]), abs(d["reciprocity_delta"])),
            f"Engagement {d['engagement_delta']:+.2f}, reciprocity {d['reciprocity_delta']:+.2f}.",
        )

    # REPAIR requires prior friction. Recovery from nothing is not repair.
    prior_friction = bool(
        _signal_set(baseline) & (tax.pressure | tax.contradiction | tax.boundary_expression)
    )
    if (
        prior_friction
        and d["reciprocity_delta"] >= cfg.repair_reciprocity_delta
        and rap <= cfg.repair_risk_points
    ):
        add(
            "REPAIR",
            d["reciprocity_delta"],
            f"Reciprocity recovered {d['reciprocity_delta']:+.2f} and risk fell {rap:+.1f} after earlier friction.",
        )

    if d["volatility_delta"] >= cfg.destabilization_volatility_delta:
        add("DESTABILIZATION", d["volatility_delta"], f"Volatility {d['volatility_delta']:+.2f}.")
    elif d["volatility_delta"] <= -cfg.destabilization_volatility_delta:
        add("STABILIZATION", d["volatility_delta"], f"Volatility {d['volatility_delta']:+.2f}.")

    if abs(d["reciprocity_delta"]) >= cfg.shift_delta:
        add("RECIPROCITY_SHIFT", d["reciprocity_delta"], f"Reciprocity {d['reciprocity_delta']:+.2f}.")

    if abs(d["boundary_delta"]) >= cfg.shift_delta:
        add("BOUNDARY_SHIFT", d["boundary_delta"], f"Pressure signals {d['boundary_delta']:+.2f}.")

    return events


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def _empty_result() -> TrajectoryResult:
    return TrajectoryResult(
        vie_t_version=VIE_T_VERSION,
        config_version=CONFIG_VERSION,
        reciprocity=0.0,
        engagement_quality=0.0,
        emotional_volatility=0.0,
        conversational_drift=None,
        boundary_pressure=0.0,
        consistency=1.0,
        escalation_pressure=0.0,
        risk_level=0.0,
        risk_acceleration_points=None,
        risk_acceleration=None,
        reciprocity_delta=None,
        engagement_delta=None,
        volatility_delta=None,
        boundary_delta=None,
        events=[],
        sticky_turn_ids=[],
        confidence=0.0,
        confidence_ceiling=0.0,
        degraded=True,
        degradation_states=["NO_TURNS"],
        reason_codes=["VIE_T_NO_TURNS"],
        turns_observed=0,
    )


def compute_trajectory(
    turns: Sequence[TurnObservation],
    config: TrajectoryConfig = DEFAULT_CONFIG,
    taxonomy: Taxonomy = EMPTY_TAXONOMY,
) -> TrajectoryResult:
    """
    Deterministic and total: never raises, never returns None.

    Reason codes:
        VIE_T_OK                  full state + deltas + events
        VIE_T_LOW_CONTEXT         below min_turns_for_state
        VIE_T_NO_BASELINE         state only; deltas unavailable
        VIE_T_MISSING_TIMESTAMPS  latency-derived refinement skipped
        VIE_T_NO_TURNS            empty input

    With EMPTY_TAXONOMY every signal-dependent metric is zero. Callers must
    inject a real taxonomy and reconcile it against the live vocabulary.
    """
    cfg = config
    tax = taxonomy
    turns = list(turns)
    n = len(turns)

    if n == 0:
        return _empty_result()

    degradation: List[str] = []
    reason_codes: List[str] = []
    ceiling = 1.0

    recent = turns[-cfg.window:]
    baseline = turns[-(cfg.window + cfg.baseline):-cfg.window]

    if n < cfg.min_turns_for_state:
        degradation.append("LOW_CONTEXT")
        reason_codes.append("VIE_T_LOW_CONTEXT")
        ceiling = min(ceiling, cfg.ceiling_low_context)

    has_deltas = n >= cfg.min_turns_for_deltas and len(baseline) > 0
    if not has_deltas:
        degradation.append("NO_BASELINE")
        reason_codes.append("VIE_T_NO_BASELINE")
        ceiling = min(ceiling, cfg.ceiling_no_baseline)

    if any(t.timestamp is None for t in turns):
        degradation.append("MISSING_TIMESTAMPS")
        reason_codes.append("VIE_T_MISSING_TIMESTAMPS")
        ceiling = min(ceiling, cfg.ceiling_missing_timestamps)

    reciprocity = _reciprocity(recent)
    engagement = _engagement_quality(recent, cfg)
    volatility = _emotional_volatility(recent, cfg)
    boundary = _boundary_pressure(recent, tax)
    consistency = _consistency(recent, tax)
    escalation = _escalation_pressure(recent, tax)
    risk_level = _clamp(_mean([_safe_risk(t, cfg) for t in recent]) / cfg.risk_scale)
    drift = _conversational_drift(recent, baseline) if has_deltas else None

    if has_deltas:
        d = {
            "risk_acceleration_points": (
                _mean([_safe_risk(t, cfg) for t in recent])
                - _mean([_safe_risk(t, cfg) for t in baseline])
            ),
            "reciprocity_delta": reciprocity - _reciprocity(baseline),
            "engagement_delta": engagement - _engagement_quality(baseline, cfg),
            "volatility_delta": volatility - _emotional_volatility(baseline, cfg),
            "boundary_delta": boundary - _boundary_pressure(baseline, tax),
        }
        events = _detect_events(recent, baseline, d, cfg, tax)
        rap: Optional[float] = round(d["risk_acceleration_points"], 2)
        ra: Optional[float] = round(
            max(-1.0, min(1.0, d["risk_acceleration_points"] / cfg.risk_scale)), 4
        )
        rec_d: Optional[float] = round(d["reciprocity_delta"], 4)
        eng_d: Optional[float] = round(d["engagement_delta"], 4)
        vol_d: Optional[float] = round(d["volatility_delta"], 4)
        bnd_d: Optional[float] = round(d["boundary_delta"], 4)
        reason_codes.append("VIE_T_OK")
    else:
        events = []
        rap = ra = rec_d = eng_d = vol_d = bnd_d = None

    # Confidence is a function of DATA SUFFICIENCY only - never severity.
    sufficiency = _clamp(n / float(cfg.min_turns_for_deltas))
    if has_deltas:
        sufficiency = _clamp(0.6 + 0.4 * _clamp(n / float(cfg.window + cfg.baseline)))
    confidence = round(min(sufficiency, ceiling), 4)

    return TrajectoryResult(
        vie_t_version=VIE_T_VERSION,
        config_version=CONFIG_VERSION,
        reciprocity=round(reciprocity, 4),
        engagement_quality=round(engagement, 4),
        emotional_volatility=round(volatility, 4),
        conversational_drift=round(drift, 4) if drift is not None else None,
        boundary_pressure=round(boundary, 4),
        consistency=round(consistency, 4),
        escalation_pressure=round(escalation, 4),
        risk_level=round(risk_level, 4),
        risk_acceleration_points=rap,
        risk_acceleration=ra,
        reciprocity_delta=rec_d,
        engagement_delta=eng_d,
        volatility_delta=vol_d,
        boundary_delta=bnd_d,
        events=events,
        sticky_turn_ids=_sticky_turns(turns, cfg, tax),
        confidence=confidence,
        confidence_ceiling=round(ceiling, 4),
        degraded=bool(degradation),
        degradation_states=degradation,
        reason_codes=reason_codes,
        turns_observed=n,
    )
