"""
Copyright © 2026 Ricky Sessums. All rights reserved.

Tests for app/glossary.py.
Run: python -m pytest tests/test_glossary.py -v
"""
import json

import pytest

from app import glossary as g

CLINICAL = ("risk", "pressure", "danger", "fraud", "coercive", "manipulation", "signal", "flag")


def _payload(**kw):
    base = {
        "presentation_mode": "connection",
        "positive_signals": ["reciprocal_engagement", "warm_reception_present"],
        "concern_signals": [],
        "flags": [],
    }
    base.update(kw)
    return base


def test_deterministic_and_pure():
    p = _payload()
    a = g.build_glossary(p)
    b = g.build_glossary(p)
    assert a == b
    assert p == _payload()  # input not mutated


def test_returns_entries_for_signals_in_read():
    out = g.build_glossary(_payload())
    keys = [e["key"] for e in out["entries"]]
    assert keys == ["reciprocal_engagement", "warm_reception_present"]
    assert out["version"] == g.SCHEMA_VERSION
    assert out["voice"] == g.CONNECTION
    assert out["catalogue_total"] == g.total_for_voice(g.CONNECTION) > 0


def test_every_known_entry_has_full_copy():
    for key, e in g._ENTRIES.items():
        assert e["voice"] in (g.CONNECTION, g.RISK), key
        assert e["title"] and e["means"] and e["matters"], key
        assert len(e["title"]) <= 48, key


def test_connection_entries_avoid_clinical_vocabulary():
    for key, e in g._ENTRIES.items():
        if e["voice"] != g.CONNECTION:
            continue
        text = " ".join([e["title"], e["means"], e["matters"]]).lower()
        hits = [w for w in CLINICAL if w in text]
        assert not hits, f"{key} leaks clinical vocabulary: {hits}"


def test_risk_copy_never_shown_in_connection_mode():
    out = g.build_glossary(_payload(positive_signals=["reciprocal_engagement"], concern_signals=["money_request"]))
    shown = {e["key"]: e for e in out["entries"]}
    assert "money_request" not in shown  # risk-voice entry suppressed in connection mode


def test_risk_mode_gets_risk_entries():
    out = g.build_glossary(_payload(presentation_mode="risk", positive_signals=[],
                                    concern_signals=["money_request", "fear_driven_urgency"]))
    assert out["voice"] == g.RISK
    assert [e["key"] for e in out["entries"]] == ["money_request", "fear_driven_urgency"]
    assert all(e["known"] for e in out["entries"])


def test_flags_field_is_read_too():
    out = g.build_glossary(_payload(presentation_mode="risk", positive_signals=[], flags=["lure_and_pivot"]))
    assert [e["key"] for e in out["entries"]] == ["lure_and_pivot"]


def test_duplicate_signals_collapse():
    out = g.build_glossary(_payload(positive_signals=["reciprocal_engagement"],
                                    concern_signals=["reciprocal_engagement"],
                                    flags=["reciprocal_engagement"]))
    assert len(out["entries"]) == 1


def test_json_string_signals_accepted():
    out = g.build_glossary(_payload(positive_signals=json.dumps(["reciprocal_engagement"])))
    assert [e["key"] for e in out["entries"]] == ["reciprocal_engagement"]


@pytest.mark.parametrize("payload", [
    None,
    "nope",
    {"presentation_mode": "connection"},
    _payload(positive_signals=[], concern_signals=[], flags=[]),
    _payload(positive_signals=["No signals detected"]),
    _payload(positive_signals=["BAD KEY", "x"]),
    _payload(positive_signals=["unknown_future_signal"]),   # unknown only -> nothing to teach
])
def test_nothing_to_explain_returns_none(payload):
    assert g.build_glossary(payload) is None


def test_unknown_key_degrades_without_inventing_meaning():
    e = g.entry_for("some_future_signal", g.RISK)
    assert e["known"] is False
    assert e["title"] == "Some future signal"
    assert e["means"] == "" and e["matters"] == ""


def test_unknown_keys_excluded_from_output():
    out = g.build_glossary(_payload(positive_signals=["reciprocal_engagement", "unknown_future_signal"]))
    assert [e["key"] for e in out["entries"]] == ["reciprocal_engagement"]


def test_entry_for_rejects_malformed_keys():
    for bad in (None, "", "Bad Key", "DROP TABLE", 7, "a" * 80):
        assert g.entry_for(bad, g.RISK) is None


def test_module_has_no_storage_or_io():
    src = open(g.__file__).read()
    for forbidden in ("get_conn", "cursor(", "INSERT", "requests.", "urllib", "datetime.now", "open("):
        assert forbidden not in src, forbidden


def test_every_analyzer_signal_is_either_known_or_degrades():
    # Vocabulary appended by app/analyzer_combined.py as of f80a521.
    live = [
        "blame_inversion", "boundary_language_present", "credential_or_sensitive_info_signal",
        "fear_driven_urgency", "hard_rejection_present", "high_intent_present",
        "initial_confusion_present", "lure_and_pivot", "money_request", "owner_identity_shift",
        "payment_before_verification", "plan_collapse_blame_inversion", "playful_engagement_present",
        "pressure_present", "property_identity_shift", "reciprocal_engagement",
        "repair_attempt_present", "sexual_directness", "sexual_reciprocity_present",
        "trust_calibration_small_ask", "verification_path_shift", "vision_building_present",
        "vulnerability_narrative_early", "warm_reception_present", "withheld_owner_verification",
    ]
    missing = [k for k in live if k not in g._ENTRIES]
    assert not missing, f"analyzer signals with no glossary entry: {missing}"
