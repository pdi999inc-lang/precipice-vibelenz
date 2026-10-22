"""Copyright © 2026 Ricky Sessums. All rights reserved.

VIE-T v0.4.0 (in-conversation trajectory, SHADOW MODE) test suite.

Covers: determinism, boundedness, fail-closed degradation and confidence
ceilings, severity-blind confidence, escalation / acceleration / withdrawal /
repair, sticky turns, the live-vocabulary taxonomy (structural drift guards),
the adapter (never raises, skips without speakers), and the shadow-mode
invariant: wiring VIE-T into /analyze-screenshots changes nothing the user sees
or any decision the engine makes.
"""
import asyncio
import json
import random
import time

import httpx
import pytest

import app.main as main
from app.vie_t import (
    DEFAULT_CONFIG,
    EMPTY_TAXONOMY,
    Taxonomy,
    TrajectoryConfig,
    TurnObservation,
    compute_trajectory,
)
from app.vie_t_adapter import MAX_TURNS, run_shadow
from app.vie_t_taxonomy import (
    BOUNDARY_EXPRESSION,
    CATEGORIZED,
    FINANCIAL,
    KNOWN_UNCATEGORIZED,
    LIVE_CONSTANTS,
    LIVE_TAXONOMY,
    MAPPED,
    PRESSURE,
    SAFETY_CRITICAL,
    reconcile,
)

TAX = LIVE_TAXONOMY


def turn(i, speaker="them", **kw):
    kw.setdefault("text_len", 120)
    kw.setdefault("question_count", 1)
    kw.setdefault("risk_score", 10.0)
    kw.setdefault("timestamp", 1000.0 + i * 60)
    return TurnObservation(turn_id=f"T{i}", speaker=speaker, **kw)


def alternating(n, **kw):
    return [turn(i, "you" if i % 2 == 0 else "them", **kw) for i in range(n)]


def traj(turns, cfg=DEFAULT_CONFIG):
    return compute_trajectory(turns, cfg, TAX)


# ---------------------------------------------------------------- fail-closed / degradation
def test_empty_input_fails_closed():
    r = traj([])
    assert r.degraded and r.confidence == 0.0 and r.events == [] and r.turns_observed == 0
    assert r.reason_codes == ["VIE_T_NO_TURNS"]


def test_low_context_has_ceiling_no_events_no_deltas():
    r = traj(alternating(3))
    assert "LOW_CONTEXT" in r.degradation_states
    assert r.confidence <= DEFAULT_CONFIG.ceiling_low_context
    assert r.events == [] and r.risk_acceleration_points is None


def test_no_baseline_degrades_but_state_is_computed():
    r = traj(alternating(6))
    assert "NO_BASELINE" in r.degradation_states
    assert r.confidence <= DEFAULT_CONFIG.ceiling_no_baseline
    assert r.reciprocity > 0


def test_missing_timestamps_ceiling():
    nots = [TurnObservation(turn_id=f"T{i}", speaker="you" if i % 2 == 0 else "them",
                            text_len=100, question_count=1, risk_score=10.0) for i in range(20)]
    r = traj(nots)
    assert "MISSING_TIMESTAMPS" in r.degradation_states
    assert r.confidence <= DEFAULT_CONFIG.ceiling_missing_timestamps


def test_confidence_independent_of_severity():
    assert traj(alternating(20, risk_score=5.0)).confidence == traj(alternating(20, risk_score=95.0)).confidence


# ---------------------------------------------------------------- determinism / bounds / fuzz
def test_deterministic():
    t = alternating(20)
    assert traj(t).to_dict() == traj(t).to_dict()


@pytest.mark.parametrize("scenario", [
    alternating(20),
    alternating(20, risk_score=100.0, text_len=99999, question_count=50),
    alternating(20, risk_score=0.0, text_len=0, question_count=0),
    [turn(i, "you") for i in range(20)],
])
def test_bounded(scenario):
    r = traj(scenario)
    for v in (r.reciprocity, r.engagement_quality, r.emotional_volatility, r.boundary_pressure,
              r.consistency, r.escalation_pressure, r.risk_level, r.confidence):
        assert 0.0 <= v <= 1.0
    if r.risk_acceleration is not None:
        for v in (r.risk_acceleration, r.reciprocity_delta, r.engagement_delta,
                  r.volatility_delta, r.boundary_delta):
            assert -1.0 <= v <= 1.0


def test_single_speaker_has_zero_reciprocity():
    assert traj([turn(i, "you") for i in range(20)]).reciprocity == 0.0


def test_fuzz_never_raises_and_stays_bounded():
    random.seed(7)
    pool = ["pressure_present", "credential_or_sensitive_info_signal", "unknown_signal",
            "owner_identity_shift", "no_pressure"]
    for _ in range(300):
        fuzz = [TurnObservation(
            turn_id=f"T{i}",
            speaker=random.choice(["you", "them", "x", ""]),
            text_len=random.choice([0, -5, 10, 10**7]),
            question_count=random.choice([0, -1, 3, 10**4]),
            risk_score=random.choice([-50.0, 0.0, 50.0, 500.0, float("inf"), float("nan")]),
            signals=random.sample(pool, k=random.randint(0, 3)),
            safety_critical=random.choice([True, False]),
            timestamp=random.choice([None, 1000.0]),
        ) for i in range(random.randint(0, 30))]
        r = traj(fuzz)
        assert 0.0 <= r.confidence <= 1.0
        assert 0.0 <= r.risk_level <= 1.0


def test_performance():
    big = alternating(20000, risk_score=30.0)
    t0 = time.perf_counter()
    traj(big)
    assert (time.perf_counter() - t0) * 1000 < 250
    small = alternating(200)
    t0 = time.perf_counter()
    for _ in range(200):
        traj(small)
    assert (time.perf_counter() - t0) * 1000 / 200 < 2.0


# ---------------------------------------------------------------- events
def _escalating_thread():
    t = alternating(8, risk_score=10.0)
    t += [
        turn(8, "them", risk_score=35.0, signals=["pressure_present"]),
        turn(9, "you", risk_score=40.0),
        turn(10, "them", risk_score=55.0, signals=["pressure_present", "isolation_from_others"]),
        turn(11, "you", risk_score=50.0),
        turn(12, "them", risk_score=75.0, signals=["money_request"], safety_critical=True),
        turn(13, "you", risk_score=60.0),
    ]
    return t


def test_escalation_detected_with_evidence_and_not_double_counted():
    r = traj(_escalating_thread())
    names = [e.event for e in r.events]
    assert "ESCALATION" in names and "ACCELERATION" not in names
    assert r.risk_acceleration_points > 10.0
    ev = next(e for e in r.events if e.event == "ESCALATION")
    assert ev.turn_ids and ("T12" in ev.turn_ids or "T10" in ev.turn_ids)


def test_acceleration_is_a_leading_indicator_not_a_level():
    flat = alternating(16, risk_score=70.0)
    climbing = [turn(i, "you" if i % 2 == 0 else "them", risk_score=5.0 + i * 4.0) for i in range(16)]
    f, c = traj(flat), traj(climbing)
    assert abs(f.risk_acceleration_points) < 1.0
    assert c.risk_acceleration_points > 15.0 and c.risk_level < f.risk_level


def test_withdrawal_detected():
    wd = alternating(8, text_len=300, question_count=2, risk_score=10.0)
    wd += [turn(i, "you", text_len=8, question_count=0, risk_score=10.0) for i in range(8, 14)]
    assert "WITHDRAWAL" in [e.event for e in traj(wd).events]


def test_repair_requires_prior_friction():
    friction = [turn(i, "you" if i % 2 == 0 else "them", risk_score=45.0, text_len=60, question_count=0,
                     signals=["pressure_present"] if i in (2, 4) else []) for i in range(8)]
    good = [turn(i, "you" if i % 2 == 0 else "them", risk_score=15.0, text_len=200, question_count=2,
                 signals=["no_pressure"]) for i in range(8, 14)]
    assert "REPAIR" in [e.event for e in traj(friction + good).events]

    calm_before = alternating(8, risk_score=45.0, text_len=60, question_count=0)
    assert "REPAIR" not in [e.event for e in traj(calm_before + good).events]


def test_boundary_expression_is_not_counted_as_pressure():
    # The subject saying "I'm not comfortable" must not raise pressure metrics.
    said_no = [turn(i, "you" if i % 2 == 0 else "them", signals=["boundary_language_present"]) for i in range(12)]
    assert traj(said_no).boundary_pressure == 0.0


def test_pressure_relief_dampener_lowers_pressure():
    base = [turn(i, "you" if i % 2 == 0 else "them", signals=["pressure_present"] if i == 8 else []) for i in range(12)]
    relieved = [turn(i, "you" if i % 2 == 0 else "them",
                     signals=["pressure_present"] if i == 8 else (["no_pressure"] if i == 9 else [])) for i in range(12)]
    assert traj(relieved).boundary_pressure < traj(base).boundary_pressure


def test_empty_taxonomy_is_blind_by_construction():
    # Proves WHY the taxonomy must be injected and reconciled: without it every
    # signal-dependent metric is silently zero.
    t = _escalating_thread()
    assert compute_trajectory(t, DEFAULT_CONFIG, EMPTY_TAXONOMY).escalation_pressure == 0.0
    assert traj(t).escalation_pressure > 0.0


# ---------------------------------------------------------------- sticky turns
def test_sticky_rules():
    st = alternating(30, risk_score=10.0)
    st[14] = turn(14, "you", risk_score=20.0, signals=["isolation_from_others"])
    st[17] = turn(17, "them", risk_score=30.0, signals=["credential_or_sensitive_info_signal"], safety_critical=True)
    r = traj(st)
    assert {"T14", "T17", "T29"} <= set(r.sticky_turn_ids)
    assert "T5" not in r.sticky_turn_ids
    assert r.sticky_turn_ids == sorted(r.sticky_turn_ids, key=lambda x: int(x[1:]))


def test_safety_never_decays_but_high_signal_does():
    cfg = TrajectoryConfig(high_signal_window=3)
    long_thread = alternating(60, risk_score=5.0)
    long_thread[2] = turn(2, "you", signals=["credential_or_sensitive_info_signal"], safety_critical=True)
    long_thread[3] = turn(3, "them", signals=["isolation_from_others"])
    r = traj(long_thread, cfg)
    assert "T2" in r.sticky_turn_ids and "T3" not in r.sticky_turn_ids


def test_grooming_progression():
    g = [
        turn(0, "them", text_len=40, risk_score=5.0), turn(1, "you", text_len=45, risk_score=5.0),
        turn(2, "them", text_len=200, risk_score=10.0), turn(3, "you", text_len=180, risk_score=10.0),
        turn(4, "them", text_len=400, risk_score=20.0), turn(5, "you", text_len=150, risk_score=20.0),
        turn(6, "them", text_len=500, risk_score=35.0, signals=["isolation_from_others"]),
        turn(7, "you", text_len=80, risk_score=35.0),
        turn(8, "them", text_len=520, risk_score=55.0, signals=["pressure_present"]),
        turn(9, "you", text_len=40, risk_score=55.0),
        turn(10, "them", text_len=600, risk_score=70.0, signals=["pressure_present", "emergency_narrative"]),
        turn(11, "you", text_len=25, risk_score=70.0),
        turn(12, "them", text_len=650, risk_score=85.0, signals=["money_request"], safety_critical=True),
        turn(13, "you", text_len=20, risk_score=85.0),
    ]
    r = traj(g)
    assert "ESCALATION" in [e.event for e in r.events]
    assert r.boundary_pressure > 0 and r.reciprocity_delta < 0
    assert {"T6", "T12"} <= set(r.sticky_turn_ids)


def test_audit_projection_is_compact_and_complete():
    af = traj(_escalating_thread()).to_audit_fields()
    assert {"vie_t_version", "config_version", "trajectory_confidence", "trajectory_degraded",
            "risk_acceleration_points", "trajectory_events", "sticky_turn_count",
            "trajectory_reason_codes", "turns_observed"} <= set(af)
    assert all(not isinstance(v, dict) for v in af.values())


# ---------------------------------------------------------------- taxonomy: structural drift guards
def test_every_live_constant_member_is_categorized_or_explicitly_listed():
    """If someone adds a signal to the live constants without deciding what it is
    to VIE-T, this fails - instead of the metrics going quietly blind."""
    for name, members in LIVE_CONSTANTS.items():
        missing = members - MAPPED
        assert not missing, f"{name} has members VIE-T cannot see: {sorted(missing)}"


def test_boundary_expression_disjoint_from_pressure():
    assert not (BOUNDARY_EXPRESSION & PRESSURE)


def test_critical_live_signals_are_safety_critical():
    assert {"payment_before_verification", "credential_or_sensitive_info_signal"} <= SAFETY_CRITICAL


def test_known_uncategorized_is_not_silently_categorized():
    # Context/positive signals must stay out of harm metrics (except the one
    # deliberately dual-listed safety read).
    overlap = (KNOWN_UNCATEGORIZED & CATEGORIZED) - {"physical_harm_disclosed"}
    assert not overlap, sorted(overlap)


def test_reconcile_reports_unmapped_and_ignores_sentinel():
    assert reconcile(["money_request", "No signals detected"])["unmapped"] == []
    assert reconcile(["brand_new_signal"])["unmapped"] == ["brand_new_signal"]


def test_eval_corpus_emits_only_mapped_signals():
    from app.analyzer_combined import _run_deterministic

    emitted = set()
    with open("evals/cases.jsonl", encoding="utf-8") as fh:
        for line in fh:
            c = json.loads(line)
            r = _run_deterministic(c["text"], c.get("relationship_type", "stranger"))
            for key in ("key_signals", "concern_signals", "flags", "key_dampeners", "positive_signals"):
                emitted.update(str(s) for s in (r.get(key) or []))
    assert reconcile(emitted)["unmapped"] == []


# ---------------------------------------------------------------- adapter
LABELED = (
    "THEM: Hey, how was your weekend?\nYOU: Pretty good, went hiking\n"
    "THEM: Nice, which trail?\nYOU: Crowders Mountain\n"
    "THEM: I need $300 in gift cards right now, please\nYOU: Why?\n"
    "THEM: If you loved me you would help, don't tell anyone\nYOU: I'm not comfortable with this"
)


def _fake_analyze(line, rel):
    low = line.lower()
    sigs = ["money_request"] if "gift cards" in low else (["pressure_present"] if "don't tell" in low else [])
    return {"risk_score": 60 if sigs else 5, "key_signals": sigs}


def test_adapter_ok_path_shape():
    out = run_shadow(LABELED, "dating", analyze=_fake_analyze)
    assert out["vie_t_status"] == "OK"
    assert out["turns_observed"] == 8
    assert out["unmapped_signals"] == []
    assert "shadow_ms" in out and "trajectory_events" in out


def test_adapter_skips_without_speaker_labels():
    assert run_shadow("hello there\nhow are you", "dating", analyze=_fake_analyze)["vie_t_status"] == "SKIPPED_NO_SPEAKER"
    assert run_shadow("", "dating", analyze=_fake_analyze)["vie_t_status"] == "SKIPPED_NO_SPEAKER"
    assert run_shadow(None, "dating", analyze=_fake_analyze)["vie_t_status"] == "SKIPPED_NO_SPEAKER"


def test_adapter_skips_single_message():
    assert run_shadow("THEM: hi", "dating", analyze=_fake_analyze)["vie_t_status"] == "SKIPPED_TOO_FEW_TURNS"


def test_adapter_total_analyzer_failure_is_an_error_not_a_calm_read():
    def boom(line, rel):
        raise RuntimeError("analyzer down")

    out = run_shadow(LABELED, "dating", analyze=boom)
    assert out["vie_t_status"] == "ERROR" and out["vie_t_error"] == "ANALYZER_UNAVAILABLE"
    assert out["analyzer_failures"] == 8


def test_adapter_partial_analyzer_failure_is_recorded():
    calls = {"n": 0}

    def flaky(line, rel):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("one bad message")
        return _fake_analyze(line, rel)

    out = run_shadow(LABELED, "dating", analyze=flaky)
    assert out["vie_t_status"] == "OK" and out["analyzer_failures"] == 1


def test_adapter_unmapped_signals_are_recorded_not_fatal():
    def odd(line, rel):
        return {"risk_score": 1, "key_signals": ["totally_new_signal"]}

    out = run_shadow(LABELED, "dating", analyze=odd)
    assert out["vie_t_status"] == "OK" and out["unmapped_signals"] == ["totally_new_signal"]


def test_adapter_caps_turns():
    many = "\n".join(f"{'THEM' if i % 2 else 'YOU'}: message {i}?" for i in range(MAX_TURNS + 50))
    assert run_shadow(many, "dating", analyze=_fake_analyze)["turns_observed"] == MAX_TURNS


def test_adapter_with_live_engine_runs_and_is_fast():
    t0 = time.perf_counter()
    out = run_shadow(LABELED, "dating")
    assert out["vie_t_status"] == "OK"
    assert (time.perf_counter() - t0) < 2.0


# ---------------------------------------------------------------- shadow-mode invariant (endpoint)
TEXT = LABELED


def _post(monkeypatch, shadow=None, enabled=True):
    captured = []
    real_audit = main.write_audit_record

    def spy(*a, **k):
        captured.append(k)
        return real_audit(*a, **k)

    real_analyze, real_interpret = main.analyze_text, main.interpret_analysis
    monkeypatch.setattr(main, "write_audit_record", spy)
    monkeypatch.setattr(main, "analyze_text", lambda *a, **k: real_analyze(*a, **{**k, "use_llm": False}))
    monkeypatch.setattr(main, "interpret_analysis", lambda *a, **k: real_interpret(*a, **{**k, "use_llm": False}))
    monkeypatch.setattr(main, "_VIE_T_SHADOW_ENABLED", enabled)
    if shadow is not None:
        monkeypatch.setattr(main, "_vie_t_shadow", shadow)

    async def run():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            return await c.post("/analyze-screenshots", data={"pasted_text": TEXT, "relationship_type": "dating"},
                                headers={"accept": "application/json"})

    resp = asyncio.run(run())
    return resp, captured


def _decision(resp):
    j = resp.json()
    return {k: j.get(k) for k in ("risk_score", "risk_level", "lane", "vie_action", "primary_label")}


def test_shadow_never_reaches_user_payload_and_is_audited(monkeypatch):
    resp, captured = _post(monkeypatch)
    assert resp.status_code == 200
    assert "vie_t" not in resp.json()
    assert not any("vie_t" in k for k in resp.json())
    assert captured and captured[-1]["vie_t"]["vie_t_status"] in ("OK", "SKIPPED_TOO_FEW_TURNS")


def test_shadow_does_not_change_any_decision(monkeypatch):
    on, _ = _post(monkeypatch, enabled=True)
    off, cap_off = _post(monkeypatch, enabled=False)
    assert on.status_code == off.status_code == 200
    assert _decision(on) == _decision(off)
    assert cap_off[-1]["vie_t"] is None


def test_shadow_failure_cannot_block_a_read(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("shadow exploded")

    resp, captured = _post(monkeypatch, shadow=boom)
    assert resp.status_code == 200
    assert captured[-1]["vie_t"]["vie_t_status"] == "ERROR"


def test_shadow_import_failure_leaves_app_serving(monkeypatch):
    resp, captured = _post(monkeypatch, shadow=None, enabled=True)
    monkeypatch.setattr(main, "_vie_t_shadow", None)

    async def run():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            return await c.post("/analyze-screenshots", data={"pasted_text": TEXT, "relationship_type": "dating"},
                                headers={"accept": "application/json"})

    assert asyncio.run(run()).status_code == 200
