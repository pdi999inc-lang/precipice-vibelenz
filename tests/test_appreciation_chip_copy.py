"""Copyright © 2026 Ricky Sessums. All rights reserved.

The appreciation chip must not generalise one thank-you into a habit.

Observed on a real read 2026-11-22: the conversation contained "Thank you for
letting me! And that was amazing", said about a single night. The written take
had the context right - "that's genuine, but it's about the night you spent
together" - while the chip rendered it as "one of the habits that keeps things
warm long-term", which the rest of the read contradicted.

The detector is not at fault: that IS an expression of appreciation, and
narrowing the regex would lose genuine cases ("thank you for letting me vent").
The copy was the overclaim, so the copy is what changed.
"""
import app.healthy_patterns as hp


def _appreciation_body():
    return hp.COPY["appreciation"][hp.CONNECTION]["body"]


def test_appreciation_copy_makes_no_habit_claim():
    body = _appreciation_body().lower()
    for overclaim in ("habit", "long-term", "long term", "keeps things warm"):
        assert overclaim not in body, f"appreciation chip still claims a pattern: {overclaim!r}"


def test_appreciation_copy_points_back_at_context():
    # The miss was context collapse, so the copy should send the reader to what
    # the thanks was actually for rather than asserting what it means.
    body = _appreciation_body().lower()
    assert "what the thanks was for" in body or "what it was for" in body


def test_a_single_thanks_still_registers():
    # The detector is unchanged; only the wording moved.
    out = hp.detect("THEM: thank you for letting me! and that was amazing\nYOU: anytime")
    assert "appreciation" in (out.get("found") or {}), out


def test_appreciation_copy_keeps_the_connection_voice():
    body = _appreciation_body().lower()
    for clinical in hp.CLINICAL_WORDS:
        assert clinical not in body, clinical
    for promise in hp.OVERPROMISE:
        assert promise not in body, promise


def test_other_chip_copy_is_untouched():
    assert "An actual day, an actual time" in hp.COPY["concrete_plans"][hp.CONNECTION]["body"]
    assert "A real apology names what they did" in hp.COPY["owned_apology"][hp.CONNECTION]["body"]
