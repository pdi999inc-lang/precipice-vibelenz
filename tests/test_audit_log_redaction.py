"""Copyright © 2026 Ricky Sessums. All rights reserved.

The [AUDIT] log line must never carry conversation content.

Nothing in the web process configured a logging handler before 2026-11-23, so every
logger.info() in app/ was discarded in production - including app/audit.py's
"[AUDIT]" record. Turning logging on restored that line, and restoring it created a
new hazard: the audit record contains result["evidence"], whose schema in
app/analyzer_combined.py is

    "evidence": {"<signal_id>": "<exact quote from conversation>"}

so a naive enable would have written users' private messages into the platform log
stream, whose retention we do not control.

Two defences, both pinned here:

  1. An ALLOWLIST (_LOG_SAFE_KEYS), not a denylist, so a field added to the record
     later cannot leak until someone remembers to exclude it.
  2. Errors are reduced to an exception CLASS name. Truncation was tried first and
     failed - "ValueError: unparseable model output near <sentence>" keeps the
     sentence well inside any sane character cap.

The full record is still returned to the caller and written to the audit file. This
governs only the log line.
"""
import json
import logging

import pytest

from app import audit

QUOTE = "if you actually cared about us youd stop seeing her"
SECOND = "everyone says that"


def _record(**over):
    rec = {
        "event": "ANALYSIS", "session_id": "s1", "request_id": "r1",
        "timestamp": "2026-11-23T04:50:00Z", "processing_time_ms": 36062,
        "image_count": 5, "ocr_char_count": 412, "risk_score": 72,
        "lane": "COERCION_RISK", "vie_action": "WARN", "confidence": 0.8,
        "signal_count": 2, "signals": ["isolation_pressure", "guilt_induction"],
        "evidence": {"isolation_pressure": QUOTE, "guilt_induction": SECOND},
        "active_combos": ["pressure_with_boundary_violation"], "degraded": False,
        "error": None,
    }
    rec.update(over)
    return rec


def _line(**over):
    return json.dumps(audit._log_projection(_record(**over)))


# ------------------------------------------------------------------- no content leaks

def test_evidence_quotes_never_reach_the_log():
    line = _line()
    assert QUOTE not in line
    assert SECOND not in line


def test_which_signals_had_evidence_is_kept_but_not_what_they_said():
    proj = audit._log_projection(_record())
    assert proj["evidence_signals"] == ["guilt_induction", "isolation_pressure"]
    assert proj["evidence_count"] == 2
    assert "evidence" not in proj


def test_a_field_added_later_does_not_leak():
    # The allowlist's whole purpose. If this ever fails, someone switched to a denylist.
    line = _line(future_field_with_text=QUOTE, another={"nested": QUOTE})
    assert QUOTE not in line
    assert "future_field_with_text" not in line


def test_unexpected_evidence_shape_shows_nothing():
    proj = audit._log_projection(_record(evidence=QUOTE))
    assert proj["evidence_count"] == -1
    assert QUOTE not in json.dumps(proj)


def test_the_returned_record_is_untouched():
    rec = _record()
    audit._log_projection(rec)
    assert rec["evidence"]["isolation_pressure"] == QUOTE, "projection mutated the record"


# --------------------------------------------------------------- errors: class only

@pytest.mark.parametrize("err,expected", [
    ("ValueError: unparseable model output near " + QUOTE, "ValueError"),
    ("RuntimeError: OCR failure on image 2", "RuntimeError"),
    ("anthropic.APIStatusError: overloaded", "anthropic.APIStatusError"),
    ("ReadTimeout: timed out", "ReadTimeout"),
    ("SomeWarning: whatever", "SomeWarning"),
    (QUOTE, "unclassified"),                      # no colon at all
    ("THEM: " + QUOTE, "unclassified"),           # colon, but prose before it
    ("my password is hunter2: failed", "unclassified"),
])
def test_error_is_reduced_to_a_class_name(err, expected):
    proj = audit._log_projection(_record(error=err))
    assert proj["error_class"] == expected
    assert QUOTE not in json.dumps(proj)
    assert "hunter2" not in json.dumps(proj)


def test_no_error_means_no_error_key():
    assert "error_class" not in audit._log_projection(_record(error=None))


def test_error_message_is_never_logged_even_truncated():
    # Truncation was the first attempt and it leaked. Guard against a revert.
    proj = audit._log_projection(_record(error="ValueError: near " + QUOTE))
    assert "error_head" not in proj
    assert "unparseable" not in json.dumps(proj)


# ------------------------------------------------------------------ diagnostics kept

def test_the_line_is_still_worth_having():
    proj = audit._log_projection(_record())
    for k in ("request_id", "lane", "risk_score", "processing_time_ms",
              "image_count", "degraded", "signals"):
        assert k in proj, k


# ------------------------------------------------------------------ logging is live

def test_application_logger_has_a_handler_and_emits_info():
    import app.main  # noqa: F401  - importing configures the vibelenz logger tree
    vl = logging.getLogger("vibelenz")
    assert vl.handlers, "no handler on the vibelenz logger - info lines go nowhere"
    assert vl.isEnabledFor(logging.INFO), "INFO disabled - the [AUDIT] line is discarded"


def test_records_still_reach_root_so_caplog_works(caplog):
    # propagate must stay on. tests/test_injection_guard.py and tests/test_literacy.py
    # capture at the root, and propagate=False silently breaks them.
    import app.main  # noqa: F401
    assert logging.getLogger("vibelenz").propagate is True
    with caplog.at_level(logging.INFO, logger="vibelenz.audit"):
        logging.getLogger("vibelenz.audit").info("[AUDIT] probe")
    assert any("[AUDIT] probe" in r.message for r in caplog.records)
