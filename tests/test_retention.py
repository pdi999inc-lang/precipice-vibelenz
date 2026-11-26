"""Copyright (c) 2026 Ricky Sessums. All rights reserved.

The retention sweep. WP3 step 2.

This job's mistakes cannot be undone, so these tests are mostly about what it must
NOT do. The four load-bearing ones:

  1. A dry run issues ZERO DELETE statements. Not "deletes nothing because the
     predicate matched nothing" - issues no DELETE at all.
  2. email_visitors is never swept. The published privacy policy says email stays
     until removal is requested; the 90-day decision covered analysis text only.
  3. A misconfigured window aborts before a connection is opened. RETENTION_DAYS=0
     would put the cutoff at now and match every row.
  4. Any error rolls back and never commits. A partial sweep is not a sweep.

PATCHING NOTE. app/retention.py does `from app.db import get_conn`, which binds the
function at import time, so patching app.db.get_conn has no effect. Patch
app.retention.get_conn. This cost a debugging cycle; it is written down so it does
not cost another.
"""
import datetime
import json

import pytest

from app import retention

CUTOFF = datetime.datetime(2026, 8, 25, tzinfo=datetime.timezone.utc)
OLDEST = datetime.datetime(2026, 4, 9, tzinfo=datetime.timezone.utc)

SWEPT_TABLES = ["analyses", "conversation_batches", "conversations",
                "predictions", "llm_call_log"]


class _Cursor:
    def __init__(self, sink, rowcount=7, table_exists=True, boom_on_delete=False):
        self.sink = sink
        self.rowcount = rowcount
        self._last = None
        self._exists = table_exists
        self._boom = boom_on_delete

    def execute(self, sql, params=None):
        if self._boom and sql.strip().upper().startswith("DELETE"):
            raise RuntimeError("db exploded mid-sweep")
        self.sink.append(" ".join(sql.split()))
        self._last = sql

    def fetchone(self):
        q = (self._last or "").lower()
        if "to_regclass" in q:
            return (self._exists,)
        if "now() -" in q:
            return (CUTOFF,)
        if "min(created_at)" in q:
            return (OLDEST,)
        return (42,)

    def close(self):
        pass


class _Conn:
    def __init__(self, sink, **kw):
        self.sink = sink
        self.actions = []
        self._kw = kw

    def cursor(self):
        return _Cursor(self.sink, **self._kw)

    def commit(self):
        self.actions.append("COMMIT")

    def rollback(self):
        self.actions.append("ROLLBACK")

    def close(self):
        self.actions.append("CLOSE")


def _run(monkeypatch, execute=False, **conn_kw):
    monkeypatch.delenv("RETENTION_DAYS", raising=False)
    sink = []
    conn = _Conn(sink, **conn_kw)
    monkeypatch.setattr(retention, "get_conn", lambda: conn)
    report = retention.sweep(execute=execute)
    return report, sink, conn


def _deletes(sink):
    return [q for q in sink if q.upper().startswith("DELETE")]


def _deleted_tables(sink):
    return [q.split("FROM ")[1].split(" ")[0] for q in _deletes(sink)]


# ------------------------------------------------------- 1. dry run writes nothing

def test_dry_run_issues_no_delete_statement_at_all(monkeypatch):
    report, sink, conn = _run(monkeypatch, execute=False)
    assert _deletes(sink) == [], f"dry run issued DELETEs: {_deletes(sink)}"
    assert report["mode"] == "dry_run"
    assert report["total_deleted"] == 0


def test_dry_run_rolls_back_and_never_commits(monkeypatch):
    _, _, conn = _run(monkeypatch, execute=False)
    assert "COMMIT" not in conn.actions
    assert "ROLLBACK" in conn.actions, "an inspection transaction was left dangling"


def test_dry_run_still_reports_what_it_would_delete(monkeypatch):
    report, _, _ = _run(monkeypatch, execute=False)
    swept = [t for t in report["tables"] if t["sweep"]]
    assert swept, "no sweepable tables reported"
    assert all("would_delete" in t for t in swept)
    assert all("oldest_surviving" in t for t in report["tables"])


# --------------------------------------------------- 2. email_visitors is untouched

def test_email_visitors_is_never_swept_in_either_mode(monkeypatch):
    for execute in (False, True):
        _, sink, _ = _run(monkeypatch, execute=execute)
        assert "email_visitors" not in _deleted_tables(sink), (
            "email retention is a separate policy decision; the published policy "
            "says email stays until removal is requested")


def test_email_visitors_is_still_counted(monkeypatch):
    report, _, _ = _run(monkeypatch, execute=False)
    row = next(t for t in report["tables"] if t["table"] == "email_visitors")
    assert row["sweep"] is False
    assert "older_than_cutoff" in row, "report-only must still mean reported"


# ------------------------------------------------------- 3. window misconfiguration

@pytest.mark.parametrize("raw", ["0", "-1", "-999", "banana", "", "   ", "1.5", "90.0"])
def test_a_bad_window_aborts_without_opening_a_connection(monkeypatch, raw):
    monkeypatch.setenv("RETENTION_DAYS", raw)

    def no_conn():
        raise AssertionError("get_conn must not be called when the window is invalid")

    monkeypatch.setattr(retention, "get_conn", no_conn)
    with pytest.raises(retention.RetentionAbort):
        retention.sweep(execute=True)


@pytest.mark.parametrize("raw,expected", [(None, 90), ("90", 90), ("1", 1), ("365", 365)])
def test_a_valid_window_is_accepted(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("RETENTION_DAYS", raising=False)
    else:
        monkeypatch.setenv("RETENTION_DAYS", raw)
    assert retention.retention_days() == expected


def test_the_default_is_ninety_days():
    assert retention.DEFAULT_RETENTION_DAYS == 90


# ----------------------------------------------------------- 4. failure rolls back

def test_an_error_mid_sweep_rolls_back_and_re_raises(monkeypatch):
    monkeypatch.delenv("RETENTION_DAYS", raising=False)
    conn = _Conn([], boom_on_delete=True)
    monkeypatch.setattr(retention, "get_conn", lambda: conn)
    with pytest.raises(RuntimeError):
        retention.sweep(execute=True)
    assert "COMMIT" not in conn.actions, "a partial sweep was committed"
    assert "ROLLBACK" in conn.actions


def test_no_database_is_not_reported_as_success(monkeypatch):
    monkeypatch.delenv("RETENTION_DAYS", raising=False)
    monkeypatch.setattr(retention, "get_conn", lambda: None)
    report = retention.sweep(execute=True)
    assert report["status"] == "no_database"
    assert report.get("total_deleted", 0) == 0


# ------------------------------------------------------------------ hold semantics

def test_every_swept_table_filters_on_the_cutoff(monkeypatch):
    _, sink, _ = _run(monkeypatch, execute=True)
    for q in _deletes(sink):
        assert "created_at < %(cutoff)s" in q, q


def test_held_analyses_are_excluded_by_the_predicate(monkeypatch):
    _, sink, _ = _run(monkeypatch, execute=True)
    q = next(x for x in _deletes(sink) if "FROM analyses" in x)
    assert "retention_hold_at IS NULL" in q


def test_a_hold_propagates_to_the_whole_conversation(monkeypatch):
    # analyses has no conversation_id, so the link runs through
    # conversation_batches.request_id. Holding one batch while deleting its siblings
    # would leave a saved read with its continuity context gutted.
    _, sink, _ = _run(monkeypatch, execute=True)
    for table in ("conversation_batches", "conversations", "predictions"):
        q = next(x for x in _deletes(sink) if f"FROM {table}" in x)
        assert "conversation_batches" in q and "retention_hold_at IS NOT NULL" in q, (
            f"{table} does not honour a conversation-level hold")


def test_predicates_use_not_exists_rather_than_not_in(monkeypatch):
    # "x NOT IN (subquery)" is NULL when the subquery yields a NULL, and the row is
    # then skipped. That happens to fail safe here, but NOT EXISTS is NULL-safe by
    # construction and does not depend on the accident.
    _, sink, _ = _run(monkeypatch, execute=True)
    for q in _deletes(sink):
        assert " NOT IN (" not in q.upper(), q


def test_all_five_content_tables_are_swept(monkeypatch):
    _, sink, _ = _run(monkeypatch, execute=True)
    assert _deleted_tables(sink) == SWEPT_TABLES


# -------------------------------------------------------------- report and output

def test_a_missing_table_is_reported_not_fatal(monkeypatch):
    report, sink, _ = _run(monkeypatch, execute=True, table_exists=False)
    assert report["status"] == "committed"
    assert all(t["status"] == "absent" for t in report["tables"])
    assert _deletes(sink) == [], "deleted from a table that does not exist"


def test_the_report_is_json_serialisable(monkeypatch):
    report, _, _ = _run(monkeypatch, execute=False)
    json.dumps(report)  # the log line does this; a TypeError here breaks the run


def test_the_report_always_carries_mode_status_and_cutoff(monkeypatch):
    for execute in (False, True):
        report, _, _ = _run(monkeypatch, execute=execute)
        for key in ("event", "mode", "status", "retention_days", "cutoff", "duration_ms"):
            assert key in report, key


def test_the_rendered_output_says_nothing_was_deleted_on_a_dry_run(monkeypatch):
    report, _, _ = _run(monkeypatch, execute=False)
    text = retention._render(report)
    assert "Nothing was deleted" in text
    assert "--execute" in text


def test_main_defaults_to_a_dry_run(monkeypatch, capsys):
    monkeypatch.delenv("RETENTION_DAYS", raising=False)
    sink = []
    conn = _Conn(sink)
    monkeypatch.setattr(retention, "get_conn", lambda: conn)
    rc = retention.main([])
    assert rc == 0
    assert _deletes(sink) == [], "python -m app.retention with no args deleted rows"


def test_main_returns_nonzero_when_the_window_is_bad(monkeypatch):
    monkeypatch.setenv("RETENTION_DAYS", "0")
    assert retention.main([]) == 2
