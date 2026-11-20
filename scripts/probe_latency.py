"""Copyright © 2026 Ricky Sessums. All rights reserved.

WP5 latency + cost probe. Measurement only - it sets no targets and changes no behavior.

    python scripts/probe_latency.py fixtures          # build synthetic screenshots (once)
    python scripts/probe_latency.py run --n 6         # run probes against the live app
    python scripts/probe_latency.py report            # p50/p95 from the local JSONL
    python scripts/probe_latency.py cleanup-sql       # print the DELETE statements

Rules this script will not break:
  * Synthetic conversations only. No real user text ever goes through a probe.
  * Every probe is tagged utm_source=vl-probe so each row can be found and deleted.
  * Fresh session per probe (no cookie reuse), so the email gate behaves as it does
    for a first-time visitor. The gate is not disabled.
  * Rate limits are respected, never disabled. VL_RATE_LIMITS_ENABLED is not touched.
    The live analyze limits are 8 per 10 minutes and 30 per day, keyed by IP, so the
    default pause is 80s and the script refuses to exceed the daily cap in one run.

What it can and cannot see: wall time is measured here; per-AI-call time and tokens
live in llm_call_log on the server. Join them by request_id with the SQL that
`report` prints. The difference between wall time and the summed AI time is
everything else (pre-classification, shadow modules, audit, DB, render, network) and
this script cannot attribute it further.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import statistics
import sys
import time
import uuid

BASE = os.environ.get("VL_PROBE_BASE", "https://app.appvibelenz.com")
TAG = "vl-probe"
HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "_probe_fixtures")
RESULTS = os.path.join(HERE, "_probe_results.jsonl")

PAUSE_S = int(os.environ.get("VL_PROBE_PAUSE_S", "80"))   # 8 per 600s -> 75s is the floor
DAILY_CAP = 30                                            # matches rate_limit.py

# Synthetic conversations. Invented, not from any real person.
CONVOS = {
    "fraud": (
        "THEM: hey! saw your profile, you seem really genuine\n"
        "YOU: thanks, how are you\n"
        "THEM: good. listen i dont usually do this but i feel a connection already\n"
        "YOU: thats fast haha\n"
        "THEM: im overseas on a work contract, signal is bad so i prefer whatsapp\n"
        "YOU: ok\n"
        "THEM: my contract pays next month but my card is frozen here. could you help\n"
        "      me with a small amount for supplies? i will pay you back double"
    ),
    "pressure": (
        "THEM: why didnt you answer me last night\n"
        "YOU: i was asleep\n"
        "THEM: you were online though. i saw\n"
        "YOU: i fell asleep with my phone on\n"
        "THEM: everyone always says that. i just think if you cared youd reply\n"
        "YOU: i do care\n"
        "THEM: then prove it. delete the app and just talk to me\n"
        "YOU: thats a lot\n"
        "THEM: its not a lot if you actually mean what you say"
    ),
    "benign": (
        "THEM: how was the hike\n"
        "YOU: good! longer than i thought, my legs are done\n"
        "THEM: haha which trail\n"
        "YOU: crowders mountain, the rocky one\n"
        "THEM: nice. i havent been in years\n"
        "YOU: you should come next time\n"
        "THEM: id like that. let me check my weekend"
    ),
}


def _stamp():
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- fixtures
def build_fixtures():
    """Render synthetic chat screenshots. Plain text bubbles - no real app chrome,
    no real conversation, nothing that could be mistaken for a user's data."""
    from PIL import Image, ImageDraw

    os.makedirs(FIXTURES, exist_ok=True)
    made = []
    for name, text in CONVOS.items():
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        # One image per convo, plus split variants so 3- and 10-image runs are realistic.
        for parts in (1, 3, 10):
            chunk = max(1, len(lines) // parts)
            for i in range(parts):
                seg = lines[i * chunk:(i + 1) * chunk] if i < parts - 1 else lines[i * chunk:]
                if not seg:
                    seg = ["(continued)"]
                img = Image.new("RGB", (720, 60 + 46 * len(seg)), (250, 246, 239))
                d = ImageDraw.Draw(img)
                d.text((18, 16), f"SYNTHETIC PROBE IMAGE - {name} {i + 1}/{parts}", fill=(150, 140, 130))
                y = 54
                for ln in seg:
                    right = ln.upper().startswith("YOU")
                    body = ln.split(":", 1)[-1].strip() or ln
                    w = min(620, 14 + 8 * len(body))
                    x = 720 - w - 18 if right else 18
                    d.rounded_rectangle([x, y, x + w, y + 34], 10,
                                        fill=(47, 126, 119) if right else (255, 255, 255),
                                        outline=(210, 204, 196))
                    d.text((x + 10, y + 9), body[:76],
                           fill=(255, 255, 255) if right else (42, 38, 34))
                    y += 46
                p = os.path.join(FIXTURES, f"{name}_{parts}_{i + 1}.png")
                img.save(p)
                made.append(p)
    print(f"wrote {len(made)} fixture images to {FIXTURES}")


def _fixture_set(name, n_images):
    return [os.path.join(FIXTURES, f"{name}_{n_images}_{i + 1}.png") for i in range(n_images)]


# --------------------------------------------------------------------------- run
def _post(path_kind, convo, n_images, run_id):
    import requests  # dev-only dependency, same one tests/test_integration.py uses

    url = (f"{BASE}/analyze-screenshots"
           f"?utm_source={TAG}&utm_medium={path_kind}&utm_campaign={run_id}")
    data = {"relationship_type": "stranger", "requested_mode": "risk",
            "user_side": "right", "analysis_mode": "standard"}
    files = []
    handles = []
    if path_kind == "paste":
        data["pasted_text"] = CONVOS[convo]
    else:
        for p in _fixture_set(convo, n_images):
            if not os.path.exists(p):
                raise SystemExit(f"missing fixture {p} - run `fixtures` first")
            h = open(p, "rb")
            handles.append(h)
            files.append(("files", (os.path.basename(p), h, "image/png")))
    t0 = time.monotonic()
    try:
        # Fresh session every probe: a bare post sends no cookies.
        r = requests.post(url, data=data, files=files or None,
                          headers={"Accept": "application/json"}, timeout=300)
        wall_ms = int((time.monotonic() - t0) * 1000)
        try:
            body = r.json()
        except Exception:
            body = {}
        return {"http": r.status_code, "wall_ms": wall_ms,
                "request_id": body.get("request_id"), "lane": body.get("lane"),
                "band": body.get("risk_level"), "degraded": body.get("degraded"),
                "llm_error": body.get("llm_error")}
    finally:
        for h in handles:
            h.close()


def run(n, plan):
    if n > DAILY_CAP:
        raise SystemExit(f"{n} probes exceeds the {DAILY_CAP}/day limit. Split across days.")
    run_id = f"wp5-{_dt.date.today().isoformat()}-{uuid.uuid4().hex[:6]}"
    print(f"run_id={run_id}  base={BASE}  probes={n}  pause={PAUSE_S}s")
    print(f"tag: utm_source={TAG} (used by cleanup-sql)\n")
    done = 0
    with open(RESULTS, "a", encoding="utf-8") as out:
        for i in range(n):
            kind, convo, n_img = plan[i % len(plan)]
            label = f"{kind}/{convo}" + (f"/{n_img}img" if kind == "shot" else "")
            try:
                rec = _post(kind, convo, n_img, run_id)
            except Exception as e:  # keep going; a failed probe is data too
                rec = {"http": None, "wall_ms": None, "error": f"{type(e).__name__}: {e}"}
            rec.update({"run_id": run_id, "idx": i + 1, "kind": kind, "convo": convo,
                        "n_images": n_img if kind == "shot" else 0, "at": _stamp()})
            out.write(json.dumps(rec) + "\n")
            out.flush()
            done += 1
            print(f"[{i + 1}/{n}] {label:22s} http={rec.get('http')} "
                  f"wall={rec.get('wall_ms')}ms lane={rec.get('lane')} "
                  f"band={rec.get('band')} {rec.get('error', '')}")
            if rec.get("http") == 429:
                print("  rate limited - stopping this batch. Resume later; do not disable limits.")
                break
            if i + 1 < n:
                time.sleep(PAUSE_S)
    print(f"\n{done} probes written to {RESULTS}")


# --------------------------------------------------------------------------- report
def _pct(vals, p):
    if not vals:
        return None
    vals = sorted(vals)
    k = max(0, min(len(vals) - 1, int(round((p / 100) * (len(vals) - 1)))))
    return vals[k]


def report():
    if not os.path.exists(RESULTS):
        raise SystemExit(f"no results at {RESULTS} - run some probes first")
    rows = [json.loads(l) for l in open(RESULTS, encoding="utf-8") if l.strip()]
    ok = [r for r in rows if r.get("http") == 200 and r.get("wall_ms")]
    print(f"{len(rows)} probes recorded, {len(ok)} successful\n")
    buckets = {}
    for r in ok:
        key = r["kind"] if r["kind"] == "paste" else f"shot/{r['n_images']}img"
        buckets.setdefault(key, []).append(r["wall_ms"])
    print(f"{'path':14s} {'n':>3s} {'p50':>8s} {'p95':>8s} {'min':>8s} {'max':>8s}")
    for key in sorted(buckets):
        v = buckets[key]
        print(f"{key:14s} {len(v):3d} {_pct(v,50):8d} {_pct(v,95):8d} {min(v):8d} {max(v):8d}")
    bad = [r for r in rows if r.get("http") not in (200, None) or r.get("error")]
    if bad:
        print(f"\n{len(bad)} non-200 or errored probes:")
        for r in bad[:10]:
            print(f"  idx={r.get('idx')} http={r.get('http')} {r.get('error','')}")
    ids = [r["request_id"] for r in ok if r.get("request_id")]
    print("\n--- run this on the database to get AI time and tokens per probe ---")
    print("SELECT a.utm_campaign, l.path, l.n_images, l.call_site, l.lane,")
    print("       l.input_tokens, l.output_tokens, l.latency_ms")
    print("  FROM llm_call_log l JOIN analyses a ON a.request_id = l.request_id")
    print(f" WHERE a.utm_source = '{TAG}' ORDER BY l.id;")
    print("\n-- summed AI time per probe vs the wall time above:")
    print("SELECT l.request_id, sum(l.latency_ms) AS ai_ms, count(*) AS calls,")
    print("       sum(l.input_tokens) AS inp, sum(l.output_tokens) AS outp")
    print("  FROM llm_call_log l JOIN analyses a ON a.request_id = l.request_id")
    print(f" WHERE a.utm_source = '{TAG}' GROUP BY l.request_id;")
    print(f"\n({len(ids)} probe request_ids recorded locally)")


def cleanup_sql():
    print("-- WP5 probe cleanup. Review the counts first, then run the deletes.")
    print(f"SELECT count(*) FROM analyses WHERE utm_source = '{TAG}';")
    print("SELECT count(*) FROM llm_call_log l JOIN analyses a")
    print(f"  ON a.request_id = l.request_id WHERE a.utm_source = '{TAG}';")
    print()
    print("DELETE FROM llm_call_log WHERE request_id IN")
    print(f"  (SELECT request_id FROM analyses WHERE utm_source = '{TAG}');")
    print(f"DELETE FROM analyses WHERE utm_source = '{TAG}';")
    print()
    print("-- conversations/batches created by probes have no utm tag; match them by time")
    print("-- window against the probe run and confirm the count before deleting anything.")


DEFAULT_PLAN = [
    ("paste", "fraud", 0), ("paste", "pressure", 0), ("paste", "benign", 0),
    ("shot", "fraud", 1), ("shot", "pressure", 3), ("shot", "benign", 10),
]

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="WP5 latency/cost probe (measurement only)")
    ap.add_argument("cmd", choices=["fixtures", "run", "report", "cleanup-sql"])
    ap.add_argument("--n", type=int, default=6, help="probes this batch (max 30/day)")
    ap.add_argument("--paste-only", action="store_true", help="skip screenshot probes")
    a = ap.parse_args()
    if a.cmd == "fixtures":
        build_fixtures()
    elif a.cmd == "run":
        plan = [p for p in DEFAULT_PLAN if p[0] == "paste"] if a.paste_only else DEFAULT_PLAN
        run(a.n, plan)
    elif a.cmd == "report":
        report()
    else:
        cleanup_sql()
