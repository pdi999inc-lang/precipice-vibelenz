# VibeLenz

Copyright © 2026 Ricky Sessums. All rights reserved.

Conversation analysis web app. A person uploads screenshots of a text conversation, or
pastes the text, and gets a read: what is going on, whether it looks like a scam or
coercion, and a sensible next step.

- **Product:** VibeLenz
- **Engine:** Verified Interaction Engine (VIE)
- **Live:** https://app.appvibelenz.com

## How a read works

1. **Input.** 1 to 10 PNG or JPEG screenshots (10 MB each), or 50 to 15,000 characters of
   pasted text. Images are handled in memory and never written to disk.
2. **Text extraction** (`app/ocr.py`). Claude vision reads each screenshot and labels the
   speakers. Tesseract is the fallback when the vision call fails.
3. **Analysis** (`app/analyzer_combined.py`). Deterministic pre-classification, then the AI
   analysis. If the AI call fails, the deterministic backup engine answers and the read is
   marked degraded.
4. **Deterministic overrides on every path.** Physical-harm override and raise-only safety
   floors (`app/safety_floors.py`). They can raise a read, never lower it.
5. **Narrative and replies** (`app/interpreter.py`, `app/reply_engine.py`). Reply
   suggestions are refused on FRAUD and COERCION_RISK reads.
6. **Record.** Every decision writes an `[AUDIT]` log line (`app/audit.py`) and a row in
   Postgres (`app/db.py`).

Shadow modules (`app/vie_t.py`, `app/consistency_gate.py`) write
to the audit log only. They never change what the user sees.

## Run locally

Python 3.12 is what production runs. Tesseract is optional (fallback OCR only).

```bash
pip install -r requirements.txt
pip install pytest requests          # tests only
uvicorn app.main:app --reload --port 8000
python -m pytest -q
```

Without `ANTHROPIC_API_KEY` the app runs on the backup engine and every read is marked
degraded. Without `DATABASE_URL` nothing is stored and follow-up questions cannot load
their conversation.

## Environment variables

Secrets live in Railway only. Never put a value in this file, in chat, or in a commit.

| Variable | Purpose | Default |
|---|---|---|
| `ANTHROPIC_API_KEY` | AI analysis, narrative, replies, vision OCR | unset: backup engine only |
| `DATABASE_URL` | Postgres | unset: nothing stored |
| `VL_ANALYSIS_MODEL`, `VL_ENRICH_MODEL`, `VL_FOLLOWUP_MODEL`, `VL_REPLY_MODEL`, `VL_VISION_MODEL` | Model per stage | see each module |
| `VL_LLM_TIMEOUT_S`, `VL_LLM_MAX_RETRIES` | Cap on each AI call | 90 seconds, 1 retry |
| `VL_PROMPT_DELIMITERS` | Fence the conversation as data in the analysis prompt | off until an eval run confirms it |
| `VL_SECURITY_HEADERS_ENABLED` | Browser security headers | on |
| `VL_RATE_LIMITS_ENABLED`, `VL_CLIENT_IP_MODE` | Per-visitor rate limits | on, `leftmost` |
| `STATS_SECRET` | Unlocks `/audit/stats` and `/diag/client-ip` | unset: both return 403 |
| `EMAIL_GATE_ENABLED`, `SENDGRID_API_KEY`, `EMAIL_FROM_ADDRESS`, `PUBLIC_BASE_URL` | Email gate and reminders | gate on; fails closed without SendGrid |
| `VIE_T_SHADOW_ENABLED` | Shadow module kill switch | on |
| `PRIVACY_CONTACT_EMAIL` | Contact shown on `/privacy` | set in code |

## Deploy

Railway auto-deploys the `main` branch. The health check is `GET /health`; if a new build
fails it, Railway keeps the previous deployment serving. `GET /health/llm` reports whether
the AI path is healthy.

Roll back with `git revert <sha>` and push. Never force-push `main`.

## Tests and evals

- `python -m pytest -q` runs offline. Tests that hit a live site are skipped unless
  `VIBELENZ_URL` is set.
- `python evals/run_eval.py` scores the eval corpus (`evals/cases.jsonl`). The corpus is
  synthetic. It measures consistency with the rules, not real-world accuracy. Run it
  before and after any prompt change.

## Claims this project does not make

No accuracy or performance percentage is published: none has been measured on real
conversations. No compliance certification is claimed.
