"""
score_outcomes.py - offline accuracy and capture-rate report for the Outcome Engine.
Copyright © 2026 Ricky Sessums. All rights reserved.

Read-only. Run locally with the production DATABASE_URL (copy it from Railway):

    $env:DATABASE_URL = "<from Railway>"
    python scripts\\score_outcomes.py

Writes evals\\outcome_report.md and prints the summary. Numbers are internal;
do not publish any accuracy claim until each prediction type has at least
MIN_SCORED scored outcomes.
"""
from __future__ import annotations

import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.outcome_scoring import score  # noqa: E402

MIN_SCORED = 30


def fetch_rows():
    import psycopg2
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set")
    conn = psycopg2.connect(url)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT conversation_id, prediction_type, window_days, created_at, outcome
            FROM predictions ORDER BY conversation_id, created_at
        """)
        return cur.fetchall()
    finally:
        conn.close()


def summarize(rows, now=None):
    """rows: (conversation_id, prediction_type, window_days, created_at, outcome)."""
    now = now or datetime.now(timezone.utc)
    by_conv = defaultdict(list)
    for r in rows:
        by_conv[r[0]].append(r)
    per_type = defaultdict(Counter)
    total = answered = returned = matured = 0
    for conv_rows in by_conv.values():
        for i, (_cid, ptype, win, created, outcome) in enumerate(conv_rows):
            total += 1
            if created and (now - created).days >= int(win or 7):
                matured += 1
            # "Returned" = the same device came back to this conversation later,
            # which is the only time the follow-up question can be shown.
            if i < len(conv_rows) - 1 or outcome:
                returned += 1
            if outcome:
                answered += 1
                per_type[ptype][score(ptype, outcome)] += 1
            else:
                per_type[ptype]["unanswered"] += 1
    return {"total": total, "matured": matured, "returned": returned,
            "answered": answered, "per_type": per_type}


def report(s):
    pct = lambda a, b: f"{(100 * a / b):.0f}%" if b else "n/a"
    L = ["# Outcome Engine report", "", "Copyright © 2026 Ricky Sessums. All rights reserved.", "",
         f"- Predictions stored: {s['total']} (past their window: {s['matured']})",
         f"- Came back to the same conversation: {s['returned']} ({pct(s['returned'], s['total'])})",
         f"- Answered the follow-up: {s['answered']} ({pct(s['answered'], s['total'])} of all, "
         f"{pct(s['answered'], s['returned'])} of those who came back)", "",
         "| Type | Hit | Miss | Unscorable | Unanswered | Accuracy (hit / scored) |",
         "|---|---|---|---|---|---|"]
    for t in ("escalation", "engagement", "fade", "steady"):
        c = s["per_type"].get(t, Counter())
        scored = c["hit"] + c["miss"]
        acc = pct(c["hit"], scored) if t != "steady" else "not scored"
        if t != "steady" and 0 < scored < MIN_SCORED:
            acc += f" (only {scored} scored - not meaningful)"
        L.append(f"| {t} | {c['hit']} | {c['miss']} | {c['unscorable']} | {c['unanswered']} | {acc} |")
    return "\n".join(L) + "\n"


def main():
    text = report(summarize(fetch_rows()))
    out = ROOT / "evals" / "outcome_report.md"
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"Saved: {out}")


if __name__ == "__main__":
    main()
