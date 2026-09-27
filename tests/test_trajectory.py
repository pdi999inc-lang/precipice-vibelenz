"""
Copyright © 2026 Ricky Sessums. All rights reserved.

Tests for app/trajectory.py.
Run: python -m pytest tests/test_trajectory.py -v
"""
import pytest

from app import trajectory as t


def _b(n, score, level="LOW"):
    return {"batch_number": n, "risk_score": score, "risk_level": level,
            "created_at": "2026-09-27T00:00:00Z"}


def _payload(batches, mode="risk", **kw):
    p = {"presentation_mode": mode, "prior_batches": batches}
    p.update(kw)
    return p


# ---------------------------------------------------------------- purity
def test_deterministic_and_does_not_mutate_input():
    p = _payload([_b(1, 20), _b(2, 40)])
    a = t.build_trajectory(p)
    b = t.build_trajectory(p)
    assert a == b
    assert p["prior_batches"] == [_b(1, 20), _b(2, 40)]


def test_module_has_no_io_or_clock():
    src = open(t.__file__).read()
    for forbidden in ("get_conn", "cursor(", "INSERT", "requests.", "urllib", "datetime.now", "random."):
        assert forbidden not in src, forbidden


# ---------------------------------------------------------------- gating
@pytest.mark.parametrize("batches", [
    [],
    [_b(1, 20)],                      # one read is not a trajectory
    None,
    "not a list",
    [{"junk": 1}],
    [{"batch_number": "x", "risk_score": 5}, {"batch_number": 2, "risk_score": 9}],
])
def test_insufficient_or_malformed_returns_none(batches):
    assert t.build_trajectory(_payload(batches)) is None


def test_degraded_continuity_returns_none():
    assert t.build_trajectory(_payload([_b(1, 10), _b(2, 80)], continuity_degraded=True)) is None


def test_non_dict_payload_returns_none():
    assert t.build_trajectory(None) is None
    assert t.build_trajectory("x") is None


def test_out_of_range_scores_dropped():
    out = t.build_trajectory(_payload([_b(1, 20), _b(2, 400), _b(3, -5)]))
    assert out is None  # only one valid batch remains


def test_duplicate_batch_numbers_collapse():
    out = t.build_trajectory(_payload([_b(1, 20), _b(1, 99), _b(2, 30)]))
    assert out["reads"] == 2
    assert out["previous_score"] == 20


def test_unordered_input_is_sorted():
    out = t.build_trajectory(_payload([_b(3, 70), _b(1, 10), _b(2, 40)]))
    assert [p["n"] for p in out["points"]] == [1, 2, 3]
    assert out["current_score"] == 70 and out["previous_score"] == 40


# ---------------------------------------------------------------- direction
@pytest.mark.parametrize("prev,cur,expected", [
    (20, 60, "toward_risk"),
    (60, 20, "easing"),
    (40, 45, "steady"),      # below MATERIAL_DELTA -> noise, not a trend
    (40, 35, "steady"),
    (40, 50, "toward_risk"), # exactly at threshold
    (50, 40, "easing"),
])
def test_direction_thresholds(prev, cur, expected):
    out = t.build_trajectory(_payload([_b(1, prev), _b(2, cur)]))
    assert out["direction"] == expected
    assert out["delta"] == cur - prev


def test_delta_since_first_tracks_whole_history():
    out = t.build_trajectory(_payload([_b(1, 10), _b(2, 50), _b(3, 30)]))
    assert out["delta_since_first"] == 20
    assert out["delta"] == -20


# ------------------------------------------------- safety asymmetry (core)
def test_easing_is_never_framed_as_safe_or_resolved():
    out = t.build_trajectory(_payload([_b(1, 90, "CRITICAL"), _b(2, 10, "LOW")]))
    text = out["summary"].lower()
    for word in ("safe", "resolved", "fine", "no longer a", "all clear", "nothing to worry"):
        assert word not in text, f"easing summary implies safety: {word}"


def test_prior_elevated_read_is_never_smoothed_away():
    out = t.build_trajectory(_payload([_b(1, 88, "HIGH"), _b(2, 12, "LOW"), _b(3, 8, "LOW")]))
    assert out["peak_elevated"] is True
    assert out["peak_risk_level"] == "HIGH"
    assert out["peak_batch"] == 1
    assert "still stands" in out["summary"]


def test_connection_voice_also_carries_the_prior_peak():
    out = t.build_trajectory(_payload([_b(1, 85, "HIGH"), _b(2, 15, "LOW")], mode="connection"))
    assert out["voice"] == t.CONNECTION
    assert "hasn't stopped being true" in out["summary"]


def test_withheld_counts_as_elevated():
    out = t.build_trajectory(_payload([_b(1, 70, "WITHHELD"), _b(2, 10, "LOW")]))
    assert out["peak_elevated"] is True


def test_all_low_history_makes_no_peak_claim():
    out = t.build_trajectory(_payload([_b(1, 10, "LOW"), _b(2, 14, "LOW")]))
    assert out["peak_elevated"] is False
    assert "still stands" not in out["summary"]


def test_sustained_escalation_is_named_as_a_pattern():
    out = t.build_trajectory(_payload([_b(1, 10), _b(2, 30), _b(3, 50), _b(4, 80)]))
    assert out["direction"] == "toward_risk"
    assert "sustained pattern" in out["summary"]


def test_toward_risk_is_always_reported_even_when_scores_are_low():
    out = t.build_trajectory(_payload([_b(1, 2, "LOW"), _b(2, 25, "LOW")]))
    assert out["direction"] == "toward_risk"


# ---------------------------------------------------------------- voice
def test_connection_voice_avoids_clinical_vocabulary():
    for prev, cur in ((20, 60), (60, 20), (40, 42)):
        out = t.build_trajectory(_payload([_b(1, prev), _b(2, cur)], mode="connection"))
        text = out["summary"].lower()
        for word in ("risk", "points", "elevated", "assessment", "signal", "flag"):
            assert word not in text, f"connection summary leaks '{word}': {out['summary']}"


def test_risk_voice_is_specific_and_numeric():
    out = t.build_trajectory(_payload([_b(1, 20), _b(2, 60)], mode="risk"))
    assert out["voice"] == t.RISK
    assert "40 points" in out["summary"]


# ---------------------------------------------------------------- shape
def test_points_are_capped_and_keep_the_latest():
    batches = [_b(i, i) for i in range(1, 30)]
    out = t.build_trajectory(_payload(batches))
    assert len(out["points"]) == t.MAX_POINTS
    assert out["points"][-1]["n"] == 29
    assert out["reads"] == 29


def test_output_contract():
    out = t.build_trajectory(_payload([_b(1, 20), _b(2, 60, "MEDIUM")]))
    expected = {
        "version", "voice", "reads", "direction", "delta", "delta_since_first",
        "current_score", "previous_score", "peak_score", "peak_risk_level",
        "peak_batch", "peak_elevated", "points", "summary",
    }
    assert set(out) == expected
    assert out["version"] == t.SCHEMA_VERSION
    assert set(out["points"][0]) == {"n", "score", "level"}


def test_no_conversation_text_in_output():
    out = t.build_trajectory(_payload([_b(1, 20), _b(2, 60)], conversation_text="SECRET"))
    assert "SECRET" not in str(out)
