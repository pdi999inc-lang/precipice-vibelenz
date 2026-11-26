"""
scripts/purge_probe_rows.py - remove synthetic probe rows from the database.

Copyright (c) 2026 Ricky Sessums. All rights reserved.

    railway run python scripts/purge_probe_rows.py              # DRY RUN
    railway run python scripts/purge_probe_rows.py --execute    # deletes

WHY. Latency, floor and repeatability probes write real rows. On 2026-11-26 the
retention dry run counted 158 analyses where 144 existed on 2026-11-22: fourteen of
them were probe traffic, not users. A retention report that counts synthetic rows
measures the operator, not the product, and the same rows would later contaminate
any calibration sample. They are removed before the retention sweep runs, not after.

SCOPE. Only rows whose analyses.utm_source = 'vl-probe'. Every probe script in this
repo and in the session tooling sets that tag, and nothing else does. Rows that were
never tagged cannot be told apart from real traffic and are deliberately LEFT ALONE -
guessing at them is how you delete a user's conversation.

DRY RUN IS THE DEFAULT, for the same reason as app/retention.py: this is
irreversible, so the safe mode is the one you get by forgetting a flag.

ORDER. Children before parents. The link from a probe analysis to its dependants
runs through request_id, so deleting the analyses first would orphan the children
and make them unfindable.

CONVERSATIONS ARE TREATED CAREFULLY. A conversations row is removed only when EVERY
batch beneath it came from a probe request and it has at least one batch. A
conversation that mixes probe and real batches, or has none, is left. The probes
used a fresh session each time so mixing should not occur, but "should not" is not a
reason to widen a DELETE.

NOT EXISTS, never NOT IN - "x NOT IN (subquery)" is NULL when the subquery yields a
NULL and the row is skipped, which is not a property to rely on in a delete.
"""
from __future__ import annotations

import argparse
import os
import sys

TAG = "vl-probe"

# Conversations entirely owned by probe requests.
_PROBE_CONVERSATIONS = """
    SELECT c.conversation_id
      FROM conversations c
     WHERE EXISTS (SELECT 1 FROM conversation_batches b
                    WHERE b.conversation_id = c.conversation_id)
       AND NOT EXISTS (
           SELECT 1 FROM conversation_batches b
            WHERE b.conversation_id = c.conversation_id
              AND NOT EXISTS (SELECT 1 FROM analyses a
                               WHERE a.request_id = b.request_id
                                 AND a.utm_source = %(tag)s))
"""

# (label, count_sql, delete_sql). Children first.
_STEPS = [
    ("llm_call_log",
     "SELECT COUNT(*) FROM llm_call_log l WHERE EXISTS ("
     " SELECT 1 FROM analyses a WHERE a.request_id = l.request_id AND a.utm_source = %(tag)s)",
     "DELETE FROM llm_call_log l WHERE EXISTS ("
     " SELECT 1 FROM analyses a WHERE a.request_id = l.request_id AND a.utm_source = %(tag)s)"),
    ("predictions",
     "SELECT COUNT(*) FROM predictions p WHERE EXISTS ("
     " SELECT 1 FROM analyses a WHERE a.request_id = p.request_id AND a.utm_source = %(tag)s)",
     "DELETE FROM predictions p WHERE EXISTS ("
     " SELECT 1 FROM analyses a WHERE a.request_id = p.request_id AND a.utm_source = %(tag)s)"),
    ("conversation_batches",
     "SELECT COUNT(*) FROM conversation_batches b WHERE EXISTS ("
     " SELECT 1 FROM analyses a WHERE a.request_id = b.request_id AND a.utm_source = %(tag)s)",
     "DELETE FROM conversation_batches b WHERE EXISTS ("
     " SELECT 1 FROM analyses a WHERE a.request_id = b.request_id AND a.utm_source = %(tag)s)"),
    ("conversations (probe-only)",
     f"SELECT COUNT(*) FROM ({_PROBE_CONVERSATIONS}) q",
     f"DELETE FROM conversations WHERE conversation_id IN ({_PROBE_CONVERSATIONS})"),
    ("analyses",
     "SELECT COUNT(*) FROM analyses WHERE utm_source = %(tag)s",
     "DELETE FROM analyses WHERE utm_source = %(tag)s"),
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Remove synthetic probe rows. Dry run unless --execute.")
    ap.add_argument("--execute", action="store_true", help="actually delete")
    args = ap.parse_args(argv)

    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("DATABASE_URL is not set. Use: railway run python scripts/purge_probe_rows.py",
              file=sys.stderr)
        return 2
    try:
        import psycopg2
    except ImportError:
        print("psycopg2 is not installed in this interpreter.", file=sys.stderr)
        return 2

    params = {"tag": TAG}
    conn = psycopg2.connect(url)
    try:
        cur = conn.cursor()

        # Campaigns present, so the operator can see WHAT is being removed before
        # anything is. Campaign names are probe run ids, not conversation content.
        cur.execute(
            "SELECT COALESCE(utm_campaign,'(none)'), COUNT(*), MIN(created_at), MAX(created_at)"
            "  FROM analyses WHERE utm_source = %(tag)s"
            " GROUP BY 1 ORDER BY 2 DESC", params)
        rows = cur.fetchall()
        print(f"\n  probe rows tagged utm_source='{TAG}', by run:\n")
        if not rows:
            print("    none found - nothing to do\n")
            conn.rollback()
            return 0
        print(f"    {'campaign':<34} {'rows':>5}  first seen")
        print(f"    {'-' * 34} {'-' * 5}  {'-' * 25}")
        for camp, n, lo, hi in rows:
            print(f"    {str(camp)[:34]:<34} {n:>5}  {lo}")

        print(f"\n  {'table':<28} {'rows':>6}")
        print(f"  {'-' * 28} {'-' * 6}")
        total = 0
        for label, count_sql, _ in _STEPS:
            cur.execute(count_sql, params)
            n = cur.fetchone()[0]
            total += n
            print(f"  {label:<28} {n:>6}")
        print(f"  {'TOTAL':<28} {total:>6}\n")

        if not args.execute:
            conn.rollback()
            print("  Nothing was deleted. Re-run with --execute to delete.\n")
            return 0

        deleted = {}
        for label, _, delete_sql in _STEPS:
            cur.execute(delete_sql, params)
            deleted[label] = cur.rowcount
        conn.commit()
        print("  DELETED")
        for label, n in deleted.items():
            print(f"  {label:<28} {n:>6}")
        print(f"  {'TOTAL':<28} {sum(deleted.values()):>6}\n")
        cur.close()
        return 0
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        print(f"\n  FAILED, rolled back: {type(exc).__name__}: {exc}\n", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
