"""Copyright © 2026 Ricky Sessums. All rights reserved."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.outcome_scoring import allowed_outcomes, score
from scripts.score_outcomes import summarize

INDEX = (Path(__file__).resolve().parent.parent / "templates" / "index.html").read_text(encoding="utf-8")


def test_safety_lane_never_offers_a_warm_answer():
    assert allowed_outcomes("escalation") == {"pressure_continued", "pressure_stopped", "no_contact"}
    assert "warmed_up" not in allowed_outcomes("escalation")


def test_every_escalation_answer_says_whether_pressure_continued_or_is_honestly_unscorable():
    assert score("escalation", "pressure_continued") == "hit"
    assert score("escalation", "pressure_stopped") == "miss"
    assert score("escalation", "no_contact") == "unscorable"


def test_mismatched_or_unknown_answers_are_never_hits():
    assert score("escalation", "warmed_up") == "unscorable"
    assert score("bogus", "warmed_up") == "unscorable"
    assert allowed_outcomes(None) == set()


def test_steady_is_never_counted_toward_accuracy():
    assert all(score("steady", o) == "unscorable" for o in ("warmed_up", "lukewarm", "went_quiet"))


def test_fade_and_engagement_scoring():
    assert score("fade", "went_quiet") == "hit" and score("fade", "warmed_up") == "miss"
    assert score("engagement", "warmed_up") == "hit" and score("engagement", "went_quiet") == "miss"


def test_summary_counts_returns_answers_and_accuracy():
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    old = now - timedelta(days=10)
    rows = [
        ("a", "escalation", 7, old, "pressure_continued"),
        ("b", "fade", 7, old, None),             # never came back
        ("c", "engagement", 3, old, None),       # came back, did not answer
        ("c", "engagement", 3, now, None),
    ]
    s = summarize(rows, now=now)
    assert s["total"] == 4 and s["answered"] == 1 and s["returned"] == 2 and s["matured"] == 3
    assert s["per_type"]["escalation"]["hit"] == 1


def test_ui_copy_is_honest_and_in_voice():
    assert "flagged fade" not in INDEX
    assert "makes your next read sharper" not in INDEX
    assert "pressure_continued" in INDEX and "pressure_stopped" in INDEX
