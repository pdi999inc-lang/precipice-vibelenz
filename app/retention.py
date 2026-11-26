"""
app/retention.py - scheduled deletion of analysis data past the retention period.

Copyright (c) 2026 Ricky Sessums. All rights reserved.

WP3 step 2. Spec and decisions: claude/WP3_SAVE_AND_RETENTION_SPEC.md.

    python -m app.retention              # DRY RUN. Reports, deletes nothing.
    python -m app.retention --execute    # Deletes. Required explicitly.
    python -m app.retention --json       # Machine-readable report.

DRY RUN IS THE DEFAULT. Deleting requires --execute. A retention sweep is the one
job in this system whose mistakes cannot be undone, so the safe mode is the one you
get by forgetting a flag.

DECIDED 2026-11-22
  - 90 days, applied RETROACTIVELY to existing records.
  - A saved analysis (analyses.retention_hold_at IS NOT NULL) is never deleted, and
    holds do not expire.
  - The policy copy ships only AFTER this job runs. A promise of deletion that
    nothing performs is worse than the current honest disclosure that there is no
    schedule.

FAIL-CLOSED DIRECTION. For a safety gate, failing closed means refusing to act. For
a deletion job the two failures point opposite ways, so the direction is chosen and
recorded: this job fails closed by NOT DELETING. Wrongful deletion is irreversible
and destroys both a user's record and the only corpus; wrongful retention is a
policy breach that the next run corrects, and the currently published policy already
discloses indefinite retention. Any error rolls the whole transaction back.

Consequence: a silently failing sweep looks identical to a sweep with nothing to do.
Every run therefore emits a report, including a zero-delete run.

HOLD PROPAGATION. analyses has no conversation_id column; the only link from an
analysis to its conversation is conversation_batches.request_id. A hold therefore
propagates to the WHOLE conversation: all its batches, its conversations row and its
predictions survive. Holding one batch while deleting its siblings would leave a
saved read with its continuity context gutted, and "compared to last time" reads
sibling batches. This is a judgement call, recorded here rather than buried.

email_visitors IS REPORT-ONLY. The published privacy policy says "Email details stay
until you ask us to remove them." The 90-day decision was about analysis text.
Sweeping email as well would be a policy change nobody made, so this job counts
those rows and deletes none of them. Deciding otherwise is a copy change first.

NOT EXISTS, not NOT IN. "x NOT IN (subquery)" evaluates to NULL when the subquery
yields a NULL, and the row is then skipped. Here that happens to fail in the safe
direction, but relying on it is fragile; NOT EXISTS is NULL-safe by construction.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional

from app.db import get_conn

logger = logging.getLogger("vibelenz.retention")

DEFAULT_RETENTION_DAYS = 90
MIN_RETENTION_DAYS = 1

# Subqueries identifying what a hold protects. Held requests come straight from the
# column; held conversations are reached through conversation_batches because
# analyses carries no conversation_id.
_HELD_REQUESTS = """
    SELECT 1 FROM analyses a
     WHERE a.retention_hold_at IS NOT NULL
       AND a.request_id = {col}
"""
_HELD_CONVERSATIONS = """
    SELECT 1 FROM conversation_batches b
      JOIN analyses a ON a.request_id = b.request_id
     WHERE a.retention_hold_at IS NOT NULL
       AND b.conversation_id = {col}
"""

# One entry per table. "where" is the DELETE predicate; everything it matches is
# older than the cutoff AND unprotected. "sweep" False means count only.
_TABLES: List[Dict[str, Any]] = [
    {
        "table": "analyses",
        "sweep": True,
        "holds": "retention_hold_at IS NOT NULL",
        "where": "created_at < %(cutoff)s AND retention_hold_at IS NULL",
        "note": "conversation_text, flags, scores, feedback",
    },
    {
        "table": "conversation_batches",
        "sweep": True,
        "holds": "EXISTS (" + _HELD_CONVERSATIONS.format(col="conversation_batches.conversation_id") + ")",
        "where": (
            "created_at < %(cutoff)s"
            " AND NOT EXISTS (" + _HELD_CONVERSATIONS.format(col="conversation_batches.conversation_id") + ")"
            " AND NOT EXISTS (" + _HELD_REQUESTS.format(col="conversation_batches.request_id") + ")"
        ),
        "note": "ocr_text - conversation content",
    },
    {
        "table": "conversations",
        "sweep": True,
        "holds": "EXISTS (" + _HELD_CONVERSATIONS.format(col="conversations.conversation_id") + ")",
        "where": (
            "created_at < %(cutoff)s"
            " AND NOT EXISTS (" + _HELD_CONVERSATIONS.format(col="conversations.conversation_id") + ")"
        ),
        "note": "continuity records",
    },
    {
        "table": "predictions",
        "sweep": True,
        "holds": "EXISTS (" + _HELD_CONVERSATIONS.format(col="predictions.conversation_id") + ")",
        "where": (
            "created_at < %(cutoff)s"
            " AND NOT EXISTS (" + _HELD_CONVERSATIONS.format(col="predictions.conversation_id") + ")"
            " AND NOT EXISTS (" + _HELD_REQUESTS.format(col="predictions.request_id") + ")"
        ),
        "note": "falsifiable predictions per conversation",
    },
    {
        "table": "llm_call_log",
        "sweep": True,
        "holds": "EXISTS (" + _HELD_REQUESTS.format(col="llm_call_log.request_id") + ")",
        "where": (
            "created_at < %(cutoff)s"
            " AND NOT EXISTS (" + _HELD_REQUESTS.format(col="llm_call_log.request_id") + ")"
        ),
        "note": "call metadata, no conversation content",
    },
    {
        "table": "email_visitors",
        "sweep": False,
        "holds": None,
        "where": "created_at < %(cutoff)s",
        "note": "REPORT ONLY - policy says email stays until removal is requested",
    },
]


class RetentionAbort(Exception):
    """Raised before any deletion when the run cannot be trusted."""


def retention_days() -> int:
    """Read and validate RETENTION_DAYS.

    A misconfigured window is the worst failure this job has: 0 or a negative value
    would put the cutoff at or after now and match every row. Anything unparseable,
    or below MIN_RETENTION_DAYS, aborts before a connection is opened.
    """
    raw = os.environ.get("RETENTION_DAYS", str(DEFAULT_RETENTION_DAYS))
    try:
        days = int(str(raw).strip())
    except (TypeError, ValueError):
        raise RetentionAbort(f"RETENTION_DAYS is not an integer: {raw!r}")
    if days < MIN_RETENTION_DAYS:
        raise RetentionAbort(
            f"RETENTION_DAYS={days} is below the minimum of {MIN_RETENTION_DAYS}; "
            "refusing to run rather than delete everything")
    return days


def _table_exists(cur, table: str) -> bool:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{table}",))
    row = cur.fetchone()
    return bool(row and row[0])


def _scalar(cur, sql: str, params: Dict[str, Any]) -> Any:
    cur.execute(sql, params)
    row = cur.fetchone()
    return row[0] if row else None


def _inspect(cur, spec: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    """Count what a table holds, without changing anything."""
    t = spec["table"]
    out: Dict[str, Any] = {"table": t, "note": spec["note"], "sweep": spec["sweep"]}
    if not _table_exists(cur, t):
        # A missing table is not an error: the sweep may run before a feature's
        # first write created it. It is reported so the absence is visible.
        out["status"] = "absent"
        return out
    out["status"] = "ok"
    out["total"] = _scalar(cur, f"SELECT COUNT(*) FROM {t}", params)
    out["older_than_cutoff"] = _scalar(
        cur, f"SELECT COUNT(*) FROM {t} WHERE created_at < %(cutoff)s", params)
    out["would_delete"] = _scalar(
        cur, f"SELECT COUNT(*) FROM {t} WHERE {spec['where']}", params) if spec["sweep"] else 0
    if spec["holds"]:
        out["held"] = _scalar(cur, f"SELECT COUNT(*) FROM {t} WHERE {spec['holds']}", params)
    oldest_kept = _scalar(
        cur,
        f"SELECT MIN(created_at) FROM {t}"
        + (f" WHERE NOT ({spec['where']})" if spec["sweep"] else ""),
        params)
    out["oldest_surviving"] = oldest_kept.isoformat() if oldest_kept else None
    # What survives. The "total" above is the count BEFORE this run, so a report
    # showing total 147 / deleted 100 never actually printed 47 anywhere - easy to
    # misread at a glance, and this job's output is the record of an irreversible
    # action. In a dry run this is what WOULD remain.
    out["remaining"] = max(0, (out.get("total") or 0) - (out.get("would_delete") or 0))
    return out


def sweep(execute: bool = False) -> Dict[str, Any]:
    """Inspect, and when execute=True delete, in one transaction.

    Returns the report. Raises RetentionAbort before opening a connection if the
    window is misconfigured. Any database error rolls everything back and re-raises:
    a partial sweep is never committed.
    """
    started = time.time()
    days = retention_days()
    report: Dict[str, Any] = {
        "event": "RETENTION_SWEEP",
        "mode": "execute" if execute else "dry_run",
        "retention_days": days,
        "tables": [],
    }

    conn = get_conn()
    if not conn:
        # No connection is not "nothing to delete" - it must never read as success.
        report["status"] = "no_database"
        report["duration_ms"] = int((time.time() - started) * 1000)
        logger.error("[RETENTION] %s", json.dumps(report))
        return report

    try:
        cur = conn.cursor()
        # One cutoff for the whole run. Computing it per statement would let a long
        # sweep use a drifting boundary.
        cutoff = _scalar(cur, "SELECT NOW() - (%(days)s || ' days')::interval",
                         {"days": days})
        params = {"cutoff": cutoff}
        report["cutoff"] = cutoff.isoformat() if cutoff else None

        for spec in _TABLES:
            info = _inspect(cur, spec, params)
            if execute and spec["sweep"] and info.get("status") == "ok":
                cur.execute(f"DELETE FROM {spec['table']} WHERE {spec['where']}", params)
                info["deleted"] = cur.rowcount
            report["tables"].append(info)

        if execute:
            conn.commit()
            report["status"] = "committed"
        else:
            # Explicit rollback even though nothing was written: the inspection
            # opened a transaction, and leaving it dangling on a pooled connection
            # is how a "dry run" ends up holding locks.
            conn.rollback()
            report["status"] = "dry_run_rolled_back"
        cur.close()
    except Exception as exc:  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        report["status"] = "rolled_back"
        report["error_class"] = type(exc).__name__
        report["duration_ms"] = int((time.time() - started) * 1000)
        logger.error("[RETENTION] %s", json.dumps(report))
        raise
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    report["duration_ms"] = int((time.time() - started) * 1000)
    report["total_deleted"] = sum(t.get("deleted", 0) for t in report["tables"])
    # Emitted on every run, including a zero-delete one, so a silently failing sweep
    # is distinguishable from a sweep with nothing to do.
    logger.info("[RETENTION] %s", json.dumps(report))
    return report


def _render(report: Dict[str, Any]) -> str:
    lines = [
        "",
        f"  VibeLenz retention sweep - {report['mode'].upper()}",
        f"  status          {report.get('status')}",
        f"  retention_days  {report.get('retention_days')}",
        f"  cutoff          {report.get('cutoff')}",
        "",
        f"  {'table':<22} {'total':>7} {'older':>7} {'held':>6} "
        f"{'delete':>7} {'keep':>7}  oldest surviving",
        f"  {'-' * 22} {'-' * 7} {'-' * 7} {'-' * 6} {'-' * 7} {'-' * 7}  {'-' * 25}",
    ]
    for t in report.get("tables", []):
        if t.get("status") == "absent":
            lines.append(f"  {t['table']:<22} {'(table does not exist)':>30}")
            continue
        verb = t.get("deleted", t.get("would_delete", 0))
        mark = "" if t.get("sweep") else "  [report only]"
        keep = t.get("remaining", t.get("total", 0))
        lines.append(
            f"  {t['table']:<22} {t.get('total', 0):>7} {t.get('older_than_cutoff', 0):>7} "
            f"{t.get('held', '-'):>6} {verb:>7} {keep:>7}  "
            f"{t.get('oldest_surviving') or '-'}{mark}")
    lines += ["", f"  duration {report.get('duration_ms')}ms", ""]
    if report["mode"] == "dry_run":
        lines += ["  Nothing was deleted. Re-run with --execute to delete.", ""]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m app.retention",
        description="Delete analysis data past the retention period. Dry run by default.")
    ap.add_argument("--execute", action="store_true",
                    help="actually delete. Without it, nothing is written.")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    try:
        report = sweep(execute=args.execute)
    except RetentionAbort as exc:
        print(f"ABORTED: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED, rolled back: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, indent=2) if args.json else _render(report))
    if report.get("status") in ("no_database", "rolled_back"):
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
