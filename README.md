# VibeLenz MVP

Conversation safety analysis API. Upload screenshots of conversations and receive a structured risk assessment.

**Built by:** Precipice Social Intelligence LLC  
**Product:** VibeLenz  
**Infrastructure:** Verified Interaction Engine (VIE)

---

## What It Does

1. User uploads 1–5 conversation screenshots (PNG/JPG)
2. System extracts text via OCR (Tesseract)
3. Deterministic rules-based analyzer scores for 10 behavioral signals
4. Returns JSON response + human-readable HTML result page

---

## Local Setup

**Requirements:** Python 3.11+, Tesseract installed locally

Install Tesseract:
- macOS: `brew install tesseract`
- Ubuntu/Debian: `sudo apt install tesseract-ocr tesseract-ocr-eng`
- Windows: https://github.com/UB-Mannheim/tesseract/wiki

Install Python dependencies:
```bash
pip install -r requirements.txt
```

Run locally:
```bash
uvicorn app.main:app --reload --port 8000
```

Open: http://localhost:8000

---

## Railway Deployment

1. Push this repo to GitHub
2. Create a new project in Railway → Deploy from GitHub repo
3. Railway auto-detects `nixpacks.toml` and installs Tesseract
4. Set no environment variables required for base deployment
5. Health check: `GET /health` → `{"status": "ok"}`

---

## API Usage

### POST /analyze-screenshots

Accepts multipart form upload. Field name: `files` (multiple).

```bash
curl -X POST https://your-railway-url/analyze-screenshots \
  -H "Accept: application/json" \
  -F "files=@screenshot1.png" \
  -F "files=@screenshot2.jpg"
```

### Response Schema

```json
{
  "request_id": "uuid",
  "timestamp": "2025-01-01T00:00:00+00:00",
  "risk_score": 68,
  "flags": ["Financial Request", "Urgency Pressure"],
  "confidence": 0.78,
  "summary": "High-risk conversation detected...",
  "recommended_action": "Stop interaction immediately...",
  "extracted_text": "raw OCR text...",
  "degraded": false
}
```

Risk score: 0–100. 0–39 = Low, 40–69 = Medium, 70–100 = High.

---

## OCR Notes

- Requires Tesseract binary on the host system
- Railway: installed automatically via `nixpacks.toml` apt packages
- If Tesseract is unavailable, OCR returns empty string and system logs a degraded warning
- Screenshots with clear, high-contrast text produce best results
- Handwritten or very small text may not extract reliably

---

## Signals Detected

| Signal | Tier | Weight |
|---|---|---|
| Financial Request | CRITICAL | 40 |
| Credential Harvest | CRITICAL | 40 |
| Platform Shift | HIGH | 20 |
| Urgency Pressure | HIGH | 20 |
| Isolation / Secrecy | HIGH | 20 |
| Emotional Manipulation | MEDIUM | 10 |
| Identity Evasion | MEDIUM | 10 |
| Investment / Crypto Scam | MEDIUM | 10 |
| Excessive Early Affection | LOW | 5 |
| Sob Story / Emergency | LOW | 5 |

---

## Architecture Constraints

This MVP does NOT include:
- ML training or model inference
- LLM calls
- Database storage
- User accounts
- Background workers
- VIE engine mesh

This is intentional. The objective is demand validation, not full VIE deployment.

© 2025 Precipice Social Intelligence LLC. All rights reserved.
