"""
audit_reads.py - read-only accuracy and health audit of stored analyses.
Copyright © 2026 Ricky Sessums. All rights reserved.

Measurement only, which the privacy policy permits ("measure whether its results
are accurate"). Prints counts and rates; never prints conversation text,
feedback notes, or emails. Run locally with the PUBLIC Postgres URL:

    $env:DATABASE_URL = Read-Host -MaskInput "Paste the URL here"
    python scripts\\audit_reads.py

Writes evals\\read_audit.md.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Date the privacy notice went live. Reads before this are permanently excluded
# from any improvement or training use under the published policy.
NOTICE_DATE = os.environ.get("VL_PRIVACY_NOTICE_DATE", "2026-09-28")

QUERIES = {
    "totals": """
        SELECT count(*),
               count(*) FILTER (WHERE NOT coalesce(degraded, false)),
               count(*) FILTER (WHERE created_at < %(notice)s),
               min(created_at)::date, max(created_at)::date
        FROM analyses""",
    "feedback": """
        SELECT coalesce(lane, 'unknown'),
               count(*) FILTER (WHERE feedback_accurate IS TRUE),
               count(*) FILTER (WHERE feedback_accurate IS FALSE),
               count(*) FILTER (WHERE feedback_accurate IS NULL),
               count(*) FILTER (WHERE feedback_note IS NOT NULL AND feedback_note <> '')
        FROM analyses GROUP BY 1 ORDER BY 1""",
    "relationship": """
        SELECT coalesce(relationship_type, 'unknown'), count(*),
               count(*) FILTER (WHERE feedback_accurate IS FALSE)
        FROM analyses GROUP BY 1 ORDER BY 2 DESC""",
    "engine": """
        SELECT coalesce(analysis_mode, 'unknown'),
               count(*),
               count(*) FILTER (WHERE llm_enriched IS TRUE),
               count(*) FILTER (WHERE llm_error IS NOT NULL AND llm_error <> ''),
               count(*) FILTER (WHERE coalesce(degraded, false))
        FROM analyses GROUP BY 1 ORDER BY 2 DESC""",
    "sources": """
        SELECT coalesce(utm_source, '(none)'), count(*)
        FROM analyses GROUP BY 1 ORDER BY 2 DESC LIMIT 10""",
    "email_join": """
        SELECT count(*), count(*) FILTER (WHERE user_email IS NOT NULL AND user_email <> '')
        FROM conversations""",
}


def run():
    import psycopg2
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set")
    conn = psycopg2.connect(url)
    conn.set_session(readonly=True)
    out = {}
    try:
        cur = conn.cursor()
        for name, sql in QUERIES.items():
            try:
                cur.execute(sql, {"notice": NOTICE_DATE})
                out[name] = cur.fetchall()
            except Exception as e:  # a missing column should not kill the audit
                conn.rollback()
                out[name] = f"query failed: {type(e).__name__}"
    finally:
        conn.close()
    return out


def pct(a, b):
    return f"{100 * a / b:.0f}%" if b else "n/a"


def report(r):
    L = ["# VibeLenz read audit", "", "Copyright © 2026 Ricky Sessums. All rights reserved.", ""]
    t = r["totals"]
    if isinstance(t, list):
        total, clean, pre, first, last = t[0]
        L += [f"- Reads: {total} ({first} to {last})",
              f"- Clean (not degraded): {clean}",
              f"- Before the privacy notice ({NOTICE_DATE}), permanently excluded from training: {pre}",
              f"- After the notice: {total - pre}", ""]
    f = r["feedback"]
    if isinstance(f, list):
        acc = sum(x[1] for x in f); inacc = sum(x[2] for x in f)
        notes = sum(x[4] for x in f)
        rated = acc + inacc
        total = sum(x[1] + x[2] + x[3] for x in f)
        L += ["## User ratings", "",
              f"- Rated: {rated} of {total} ({pct(rated, total)})",
              f"- Rated accurate: {acc} ({pct(acc, rated)} of rated); not accurate: {inacc}",
              f"- Ratings with a note: {notes} (notes are not printed)",
              "", "| Lane | Accurate | Not accurate | Unrated |", "|---|---|---|---|"]
        L += [f"| {x[0]} | {x[1]} | {x[2]} | {x[3]} |" for x in f]
        L.append("")
    for name, head, cols in [
        ("relationship", "Relationship types", "| Type | Reads | Rated not accurate |\n|---|---|---|"),
        ("engine", "Engine health", "| Mode | Reads | AI-enriched | AI error | Degraded |\n|---|---|---|---|---|"),
        ("sources", "Traffic sources (utm_source)", "| Source | Reads |\n|---|---|"),
    ]:
        L += [f"## {head}", ""]
        if isinstance(r[name], list):
            L += [cols] + ["| " + " | ".join(str(c) for c in row) + " |" for row in r[name]]
        else:
            L.append(r[name])
        L.append("")
    e = r["email_join"]
    if isinstance(e, list):
        convs, with_email = e[0]
        L += ["## Privacy check", "",
              f"- Conversations with an email attached: {with_email} of {convs}"
              + ("  <-- conversations are linked to email identities; review against the no-join rule"
                 if with_email else "")]
    return "\n".join(L) + "\n"


def main():
    text = report(run())
    out = ROOT / "evals" / "read_audit.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"Saved: {out}")


if __name__ == "__main__":
    main()
