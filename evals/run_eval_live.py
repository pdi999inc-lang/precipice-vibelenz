"""
run_eval_live.py — progress + resumable wrapper around evals/run_eval.py's run_case.
Copyright (c) 2026 Ricky Sessums. All rights reserved.

- Prints one line per case as it finishes (so a long run never looks frozen).
- Appends each result to evals/results_<label>.jsonl immediately (Ctrl+C loses nothing).
- Re-running with the same --label skips cases already done (resume).
- Writes evals/report_<label>.md at the end, or on Ctrl+C with what finished.

Usage (from anywhere; key + model env vars set in the same window):
    python evals/run_eval_live.py --label floors_sonnet
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(os.environ.get("VL_REPO", Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(REPO))
os.chdir(REPO)

from evals.run_eval import run_case  # noqa: E402


def write_report(rows, label, total):
    passed = sum(r["passed"] for r in rows)
    lines = [f"# VibeLenz eval — {label}", "", "Copyright © 2026 Ricky Sessums. All rights reserved.", "",
             f"- Models: {{'VL_ANALYSIS_MODEL': '{os.environ.get('VL_ANALYSIS_MODEL', 'default')}', "
             f"'VL_ENRICH_MODEL': '{os.environ.get('VL_ENRICH_MODEL', 'default')}'}}",
             f"- Completed: {len(rows)}/{total}",
             f"- Passed: **{passed}/{len(rows)}**",
             f"- Fell back to backup engine: {sum(1 for r in rows if r.get('fallback'))}", "",
             "| Case | Pass | Lane | Score | Secs | Failed checks |", "|---|---|---|---|---|---|"]
    for r in rows:
        failed = ", ".join(k for k, v in r["checks"].items() if not v) or "—"
        fb = " (fallback)" if r.get("fallback") else ""
        lines.append(f"| {r['id']} | {'✅' if r['passed'] else '❌'} | {r['lane']}{fb} | {r['score']} | {r['secs']} | {failed} |")
    lines += ["", "## Diagnoses", ""]
    for r in rows:
        lines.append(f"**{r['id']}** — {r.get('diagnosis', '')}" + (f"  \n_llm_error: {r['llm_error']}_" if r.get("llm_error") else ""))
        lines.append("")
    out = REPO / f"evals/report_{label}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out, passed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="run")
    ap.add_argument("--cases", default="evals/cases.jsonl")
    a = ap.parse_args()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set in this window.")
    cases = [json.loads(l) for l in (REPO / a.cases).read_text(encoding="utf-8").splitlines() if l.strip()]
    res_path = REPO / f"evals/results_{a.label}.jsonl"
    done = {}
    if res_path.exists():
        for l in res_path.read_text(encoding="utf-8").splitlines():
            if l.strip():
                r = json.loads(l)
                done[r["id"]] = r
    rows = [done[c["id"]] for c in cases if c["id"] in done]
    todo = [c for c in cases if c["id"] not in done]
    total = len(cases)
    print(f"{len(rows)} already done, {len(todo)} to run. Ctrl+C is safe: progress is saved.", flush=True)

    import logging
    fallbacks = []

    class _FB(logging.Handler):
        def emit(self, rec):
            if "falling back to deterministic" in rec.getMessage():
                fallbacks.append(rec.getMessage())
    logging.getLogger().addHandler(_FB())

    try:
        for i, c in enumerate(todo, len(rows) + 1):
            n0 = len(fallbacks)
            t0 = time.time()
            try:
                r = run_case(c, True)
            except Exception as e:  # noqa: BLE001
                r = {"id": c["id"], "lane": "ERROR", "score": None, "secs": round(time.time() - t0, 1),
                     "checks": {"ran": False}, "passed": False, "llm_error": f"{type(e).__name__}: {e}"[:200]}
            r["fallback"] = len(fallbacks) > n0
            rows.append(r)
            with res_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
            failed = ", ".join(k for k, v in r["checks"].items() if not v)
            mark = "PASS" if r["passed"] else "FAIL"
            print(f"[{i}/{total}] {mark} {r['id']:<40} {r['lane']}{' (fallback)' if r['fallback'] else ''} "
                  f"score={r['score']} {r['secs']}s {failed}", flush=True)
    except KeyboardInterrupt:
        print("\nStopped. Progress saved; re-run the same command to resume.", flush=True)
    out, passed = write_report(rows, a.label, total)
    print(f"\nPassed {passed}/{len(rows)} (of {total}). Report: {out}", flush=True)


if __name__ == "__main__":
    main()
