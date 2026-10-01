"""
VibeLenz evaluation harness.
Copyright © 2026 Ricky Sessums. All rights reserved.

Runs every case in evals/cases.jsonl through the real pipeline
(analyze_text -> interpret_analysis) and writes a markdown scorecard.

Usage (from repo root, ANTHROPIC_API_KEY set):
    python evals/run_eval.py                  # models from env (defaults = Haiku)
    python evals/run_eval.py --label sonnet   # just names the report
    python evals/run_eval.py --no-llm         # deterministic engine only (free)

Compare models by setting VL_ANALYSIS_MODEL / VL_ENRICH_MODEL before each run.
Cases are synthetic by design: the privacy policy bars using stored user
conversations to improve VibeLenz until a redaction step exists.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.analyzer_combined import analyze_text  # noqa: E402
from app.interpreter import interpret_analysis  # noqa: E402

import re

CLINICAL = ["risk", "pressure", "danger", "flag", "signal", "elevated", "assessment"]
# A clinical word right after a negation ("no pressure", "nothing points to games or
# pressure", "zero red flags") reassures rather than alarms, so it is allowed.
_NEGATION = r"\b(?:no|not|nothing|zero|without|never|any|n't)\b(?:\W+\w+){0,7}\W+"
ROMANCE = ["flirt", "romantic", "chemistry", "crush", "dating", "date night", "attraction"]


def _clinical_hits(text):
    hits = []
    for w in CLINICAL:
        for m in re.finditer(r"\b" + w, text):
            before = text[max(0, m.start() - 60):m.start()]
            if not re.search(_NEGATION + r"$", before):
                hits.append(w)
    return hits
NARRATIVE_KEYS = ["diagnosis", "reasoning", "practical_next_steps", "accountability"]


def _list(v):
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except Exception:
            return [v]
    return list(v or [])


def run_case(case, use_llm):
    t0 = time.time()
    res = analyze_text(case["text"], relationship_type=case["relationship_type"], use_llm=use_llm)
    out = interpret_analysis(res, extracted_text=case["text"],
                             relationship_type=case["relationship_type"],
                             requested_mode=case.get("mode", "risk"), use_llm=use_llm)
    secs = time.time() - t0

    lane = str(out.get("lane") or res.get("lane"))
    score = out.get("risk_score", res.get("risk_score"))
    signals = set(_list(out.get("concern_signals")) + _list(out.get("key_signals"))
                  + _list(res.get("concern_signals")) + _list(res.get("key_signals")))
    checks = {}
    checks["lane"] = lane in case["expect_lanes"]
    if "min_risk" in case:
        checks["min_risk"] = score is not None and float(score) >= case["min_risk"]
    if "max_risk" in case:
        checks["max_risk"] = score is not None and float(score) <= case["max_risk"]
    if "expect_signals_any" in case:
        checks["signals"] = bool(signals & set(case["expect_signals_any"]))
    if lane != "BLOCKED":
        checks["narrative_complete"] = all(str(out.get(k) or "").strip() for k in NARRATIVE_KEYS)
    if out.get("presentation_mode") == "connection" and lane not in ("FRAUD", "COERCION_RISK"):
        text = " ".join(str(out.get(k) or "") for k in NARRATIVE_KEYS).lower()
        checks["voice"] = not _clinical_hits(text)
    if case.get("no_romance"):
        text_all = " ".join(str(out.get(k) or "") for k in NARRATIVE_KEYS + ["human_label", "interest_summary", "social_tone"]).lower()
        checks["no_romance"] = not any(w in text_all for w in ROMANCE)
    return {
        "id": case["id"], "lane": lane, "score": score, "secs": round(secs, 1),
        "enriched": out.get("llm_enriched"), "llm_error": out.get("llm_error"),
        "degraded": out.get("degraded"), "checks": checks,
        "passed": all(checks.values()),
        "diagnosis": str(out.get("diagnosis") or "")[:300],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="run")
    ap.add_argument("--no-llm", action="store_true")
    a = ap.parse_args()
    use_llm = not a.no_llm
    cases = [json.loads(l) for l in (ROOT / "evals/cases.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    rows = [run_case(c, use_llm) for c in cases]

    models = {k: os.environ.get(k, "default(haiku)") for k in
              ["VL_ANALYSIS_MODEL", "VL_ENRICH_MODEL"]} if use_llm else {"mode": "deterministic"}
    passed = sum(r["passed"] for r in rows)
    lines = [f"# VibeLenz eval — {a.label}", "",
             "Copyright © 2026 Ricky Sessums. All rights reserved.", "",
             f"- Models: {models}",
             f"- Passed: **{passed}/{len(rows)}**",
             f"- Avg seconds/case: {sum(r['secs'] for r in rows)/len(rows):.1f}",
             f"- LLM-enriched: {sum(bool(r['enriched']) for r in rows)}/{len(rows)}", "",
             "| Case | Pass | Lane | Score | Secs | Failed checks |",
             "|---|---|---|---|---|---|"]
    for r in rows:
        failed = ", ".join(k for k, v in r["checks"].items() if not v) or "—"
        lines.append(f"| {r['id']} | {'✅' if r['passed'] else '❌'} | {r['lane']} | {r['score']} | {r['secs']} | {failed} |")
    lines += ["", "## Diagnoses (read these — specificity is judged by you, not the script)", ""]
    for r in rows:
        lines.append(f"**{r['id']}** — {r['diagnosis']}" + (f"  \n_llm_error: {r['llm_error']}_" if r["llm_error"] else ""))
        lines.append("")
    out = ROOT / f"evals/report_{a.label}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:8]))
    print(f"\nFull report: {out}")


if __name__ == "__main__":
    main()
