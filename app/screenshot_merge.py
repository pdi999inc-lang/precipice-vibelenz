"""
screenshot_merge.py — remove the lines that overlapping screenshots repeat.

Copyright (c) 2026 Ricky Sessums. All rights reserved.

WHY (2026-11-01, two live reads in a row)
  People scroll and screenshot with overlap, so the last bubble of one image is
  the first bubble of the next. OCR transcribes it twice and the analysis then
  blames the user: "you asked the same question twice", "you're resending the
  same lines to different people". That is a false accusation produced by the
  pipeline, not by the user.

DAG PLACEMENT
  OCR (per screenshot) -> merge_chunks -> analysis / per-screenshot arc.

MVP SCOPE
  For each adjacent pair of screenshots, find the longest run of lines that ends
  one and starts the other (either direction, since uploads can be newest-first),
  and drop the repeat from the later chunk. Lines match after normalizing case,
  whitespace, quotes and trailing punctuation; a line cropped at the screen edge
  matches if one is a prefix of the other (>= 12 chars). A single-line overlap
  only counts when the line is substantive (>= 10 normalized chars), so a lone
  "lol" / "Yes" at a boundary is never dropped.

INVARIANTS
  - Only removes lines at screenshot boundaries; never reorders, never touches
    the interior of a screenshot.
  - Speaker labels are part of the match (YOU: x != THEM: x).
  - Any error -> chunks returned unchanged (fail open to the old behavior, which
    is the status quo, not a safety regression).

AUDIT
  info = {"overlaps_removed": n, "pairs": [...]} — counts only, logged by main.
"""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

MAX_OVERLAP = 8
MIN_SINGLE = 10
MIN_PREFIX = 12

_Q = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "…": "..."})


def _norm(line: str) -> str:
    s = str(line or "").translate(_Q).lower()
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"[\s.!?,;:~]+$", "", s)
    return s


def _speaker(n: str) -> str:
    m = re.match(r"^(you|them|left|right|me):", n)
    return m.group(1) if m else ""


def _body(n: str) -> str:
    sp = _speaker(n)
    return n[len(sp) + 1:].strip() if sp else n


def _same(a: str, b: str) -> bool:
    """Equal, or one is the other cropped at the screen edge (prefix or suffix)."""
    if not a or not b:
        return False
    if a == b:
        return True
    if _speaker(a) != _speaker(b):
        return False
    sa, sb = _body(a), _body(b)
    short, long_ = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
    return len(short) >= MIN_PREFIX and (long_.startswith(short) or long_.endswith(short))


def _lines(chunk: str) -> List[str]:
    return [l for l in (chunk or "").splitlines() if l.strip()]


def _overlap(tail: List[str], head: List[str]) -> int:
    """Largest k with tail[-k:] ~= head[:k]."""
    best = 0
    for k in range(1, min(MAX_OVERLAP, len(tail), len(head)) + 1):
        if all(_same(_norm(tail[-k + i]), _norm(head[i])) for i in range(k)):
            if k == 1 and len(_norm(head[0])) < MIN_SINGLE:
                continue
            best = k
    return best


def merge_chunks(chunks: List[str]) -> Tuple[List[str], Dict]:
    try:
        cleaned = [_lines(c) for c in chunks]
        removed = 0
        pairs = []
        for i in range(1, len(cleaned)):
            prev, cur = cleaned[i - 1], cleaned[i]
            if not prev or not cur:
                continue
            fwd = _overlap(prev, cur)        # prev ends where cur starts (oldest-first upload)
            rev = _overlap(cur, prev)        # cur ends where prev starts (newest-first upload)
            if fwd >= rev and fwd:
                cleaned[i] = cur[fwd:]
                removed += fwd
                pairs.append({"pair": [i - 1, i], "dropped": fwd, "where": "head"})
            elif rev:
                cleaned[i] = cur[:-rev]
                removed += rev
                pairs.append({"pair": [i - 1, i], "dropped": rev, "where": "tail"})
        out = ["\n".join(ls) for ls in cleaned]
        return out, {"overlaps_removed": removed, "pairs": pairs}
    except Exception as e:  # noqa: BLE001
        return list(chunks), {"overlaps_removed": 0, "error": type(e).__name__}
