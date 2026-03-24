import logging
import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.analyzer import analyze_text
from app.audit import write_audit_record
from app.degradation import assess_degradation, apply_degradation
from app.ocr import extract_text_from_images
from app.schemas import AnalysisResponse, ErrorResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("vibelenz")

app = FastAPI(
    title="VibeLenz",
    description="Conversation safety analysis API",
    version="1.0.0",
)

templates = Jinja2Templates(directory="templates")

def _estimate_interest(result: dict, extracted_text: str) -> dict:
    text = (extracted_text or "").lower()

    score = 50

    positive_keywords = [
        "haha", "lol", "lmao", "cute", "handsome", "beautiful", "miss you",
        "want to see you", "when can i see you", "what about you", "you?",
        "😊", "😉", "😘", "😍", "❤️"
    ]

    negative_keywords = [
        "k", "ok", "sure", "yeah", "whatever", "idk"
    ]

    low_interest_phrases = [
        "busy", "can't", "cant", "maybe", "we'll see", "well see", "not sure"
    ]

    if any(k in text for k in positive_keywords):
        score += 15

    if text.count("?") >= 2:
        score += 12
    elif "?" in text:
        score += 6

    if "what about you" in text or "you?" in text:
        score += 10

    if any(k in text for k in negative_keywords):
        score -= 8

    if any(k in text for k in low_interest_phrases):
        score -= 10

    if len(text.strip()) < 80:
        score -= 8

    positive_signals = result.get("positive_signals") or []
    if "reciprocal_engagement" in positive_signals:
        score += 12
    if "boundary_respect" in positive_signals:
        score += 4
    if "transparent_intentions" in positive_signals:
        score += 5

    flags = [str(x).lower() for x in (result.get("flags") or [])]
    if "stonewalling_as_punishment" in flags:
        score -= 18
    if "passive_aggression" in flags:
        score -= 10
    if "moving_goalposts" in flags:
        score -= 12
    if "verification_avoidance" in flags:
        score -= 6

    score = max(0, min(100, int(score)))

    if score >= 70:
        label = "High interest"
    elif score >= 45:
        label = "Moderate interest"
    else:
        label = "Low interest"

    result["interest_score"] = score
    result["interest_label"] = label
    return result



def _downgrade_false_positive_grooming(result: dict, relationship_type: str) -> dict:
    # Full temporary disable of grooming classification in MVP output

    result["phase"] = "NONE"
    result["vie_action"] = "MONITOR"

    try:
        result["risk_score"] = min(int(result.get("risk_score", 0) or 0), 24)
    except Exception:
        result["risk_score"] = 24

    result["flags"] = [
        f for f in (result.get("flags") or [])
        if "groom" not in str(f).lower()
    ]

    result["active_combos"] = [
        c for c in (result.get("active_combos") or [])
        if "groom" not in str(c).lower()
    ]

    if "groom" in str(result.get("summary", "")).lower():
        result["summary"] = (
            "Conversation shows flirtation or early engagement patterns. "
            "No grooming classification is applied in this beta version."
        )

    positive = result.get("positive_signals") or []
    if "reciprocal_engagement" not in positive:
        positive.append("reciprocal_engagement")
    result["positive_signals"] = positive

    return result
    relationship_type = str(relationship_type or "").lower()
    if relationship_type not in {"dating", "family", "friend", "business"}:
        return result

    flags = result.get("flags") or []
    active_combos = result.get("active_combos") or []
    evidence = result.get("evidence") or {}
    summary = str(result.get("summary") or "")
    phase = str(result.get("phase") or "").upper()

    def norm_text(x):
        return str(x).strip().lower()

    norm_flags = [norm_text(x) for x in flags]
    combos_text = " ".join(norm_text(x) for x in active_combos)
    evidence_text = " ".join(norm_text(v) for v in evidence.values()) if isinstance(evidence, dict) else norm_text(evidence)
    combined = " ".join(norm_flags) + " " + combos_text + " " + evidence_text + " " + summary.lower() + " " + phase.lower()

    hard_indicators = [
        "money", "gift card", "wire", "paypal", "venmo", "cashapp", "bitcoin", "crypto",
        "blackmail", "threat", "coerc", "isolation", "secrecy", "minor", "underage",
        "age gap", "power imbalance", "extort", "exploit", "emergency", "travel fee",
        "dependency", "conditioning", "repeated manipulation"
    ]

    soft_flags = {
        "accidental_contact_opener",
        "platform_migration_early",
        "love_bomb_velocity",
        "verification_avoidance",
    }

    has_hard = any(word in combined for word in hard_indicators)
    has_grooming_surface = (
        phase == "GROOMING" or
        "groom" in combined or
        "predat" in combined or
        "romance scam early stage" in combined
    )

    flags_are_soft_only = len(norm_flags) > 0 and set(norm_flags).issubset(soft_flags)

    if has_grooming_surface and flags_are_soft_only and not has_hard:
        result["phase"] = "NONE"
        result["vie_action"] = "MONITOR"
        try:
            result["risk_score"] = min(int(result.get("risk_score", 0) or 0), 24)
        except Exception:
            result["risk_score"] = 24

        result["flags"] = ["uncertain_identity", "rapid_flirtation", "needs_verification"]
        result["active_combos"] = []
        result["summary"] = "Conversation shows identity ambiguity and quick flirtation escalation, but no clear financial coercion or exploitative behavior in the visible exchange."

        positive = result.get("positive_signals") or []
        if "reciprocal_engagement" not in positive:
            positive.append("reciprocal_engagement")
        result["positive_signals"] = positive

    return result


ALLOWED_TYPES = {"image/png", "image/jpeg", "image/jpg"}
MAX_FILES = 5


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/pitch", response_class=HTMLResponse)
async def pitch(request: Request):
    return templates.TemplateResponse("pitch.html", {"request": request})


@app.get("/about", response_class=HTMLResponse)
async def about(request: Request):
    return templates.TemplateResponse("about.html", {"request": request})


@app.get("/static/og-image.svg")
async def og_image():
    from fastapi.responses import Response
    svg = """<svg width="1200" height="630" viewBox="0 0 1200 630" xmlns="http://www.w3.org/2000/svg"><rect width="1200" height="630" fill="#0a0a0f"/><rect width="1200" height="5" fill="#2563eb"/><text x="80" y="112" font-family="Segoe UI,sans-serif" font-size="48" font-weight="800" fill="#ffffff">Vibe</text><text x="178" y="112" font-family="Segoe UI,sans-serif" font-size="48" font-weight="800" fill="#60a5fa">Lenz</text><text x="80" y="190" font-family="Segoe UI,sans-serif" font-size="56" font-weight="700" fill="#ffffff">Are they genuinely interested</text><text x="80" y="258" font-family="Segoe UI,sans-serif" font-size="56" font-weight="700" fill="#60a5fa">or just going through the motions?</text><text x="80" y="312" font-family="Segoe UI,sans-serif" font-size="22" fill="#7a9cc4">Upload a screenshot. Get a clear read on what is really happening.</text><rect x="80" y="492" width="520" height="96" rx="12" fill="#2563eb"/><text x="340" y="548" font-family="Segoe UI,sans-serif" font-size="34" font-weight="700" fill="#ffffff" text-anchor="middle">appvibelenz.com</text></svg>"""
    return Response(content=svg, media_type="image/svg+xml")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/audit/stats")
async def audit_stats():
    from app.audit import get_session_stats
    return get_session_stats()


@app.post("/analyze-screenshots")
async def analyze_screenshots(
    request: Request,
    files: List[UploadFile] = File(...),
    relationship_type: str = "stranger",
    context_note: str = "",
):
    request_id = str(uuid.uuid4())
    ts = datetime.now(timezone.utc).isoformat()
    import time
    timestamp_start = time.time()

    logger.info(f"[{request_id}] Received {len(files)} file(s) at {ts}")

    # --- Validation ---
    if len(files) > MAX_FILES:
        logger.warning(f"[{request_id}] Rejected: too many files ({len(files)})")
        raise HTTPException(
            status_code=422,
            detail=f"Maximum {MAX_FILES} files allowed. Received {len(files)}.",
        )

    for f in files:
        if f.content_type not in ALLOWED_TYPES:
            logger.warning(f"[{request_id}] Rejected: unsupported type {f.content_type}")
            raise HTTPException(
                status_code=422,
                detail=f"Unsupported file type: {f.content_type}. Allowed: png, jpg, jpeg.",
            )

    # --- OCR ---
    try:
        image_bytes_list = [await f.read() for f in files]
        extracted_text = extract_text_from_images(image_bytes_list)
    except Exception as e:
        logger.error(f"[{request_id}] OCR failure: {e}")
        raise HTTPException(status_code=503, detail="OCR processing failed. System blocked.")

    if not extracted_text.strip():
        logger.warning(f"[{request_id}] Empty OCR result")
        extracted_text = "[No readable text detected in uploaded images]"

    # --- Analysis ---
    try:
        result = analyze_text(extracted_text, relationship_type=relationship_type)
        result = _downgrade_false_positive_grooming(result, relationship_type)
        result = _estimate_interest(result, extracted_text)
    except Exception as e:
        logger.error(f"[{request_id}] Analysis failure: {e}")
        raise HTTPException(status_code=503, detail="Analysis engine failed. System blocked.")
    # FINAL OUTPUT SANITIZATION (last step before user-facing response)
    blocked_terms = ("groom", "predat")

    def _contains_blocked(value):
        return any(term in str(value).lower() for term in blocked_terms)

    if _contains_blocked(result.get("phase", "")):
        result["phase"] = "NONE"

    result["flags"] = [
        f for f in (result.get("flags") or [])
        if not _contains_blocked(f)
    ]

    result["active_combos"] = [
        c for c in (result.get("active_combos") or [])
        if not _contains_blocked(c)
    ]

    if _contains_blocked(result.get("summary", "")):
        result["summary"] = (
            "Conversation shows flirtation or early engagement patterns. "
            "No grooming classification is applied in this beta version."
        )

    if str(result.get("vie_action", "")).upper() == "BLOCK":
        result["vie_action"] = "MONITOR"

    try:
        result["risk_score"] = min(int(result.get("risk_score", 0) or 0), 24)
    except Exception:
        result["risk_score"] = 24

    response_payload = AnalysisResponse(
        request_id=request_id,
        timestamp=ts,
        risk_score=result["risk_score"],
        flags=result["flags"],
        confidence=result["confidence"],
        summary=result["summary"],
        recommended_action=result["recommended_action"],
        extracted_text=extracted_text,
        degraded=result.get("degraded", False),
    )

    logger.info(
        f"[{request_id}] Risk={result['risk_score']} Flags={result['flags']} Degraded={result.get('degraded', False)}"
    )

    # Assess degradation
    assessment = assess_degradation(
        ocr_char_count=len(extracted_text),
        confidence=result.get("confidence", 0.5),
        processing_time_ms=int((time.time() - timestamp_start) * 1000),
        result_degraded=result.get("degraded", False),
    )
    result = apply_degradation(result, assessment)

    # Write structured audit record
    write_audit_record(
        request_id=request_id,
        timestamp_start=timestamp_start,
        image_count=len(files),
        ocr_char_count=len(extracted_text),
        result=result,
        degraded=result.get("degraded", False),
    )

    accept = request.headers.get("accept", "")
    if "application/json" in accept or "text/html" not in accept:
        return JSONResponse(content=response_payload.model_dump())

    risk_label = "Low"
    if result["risk_score"] >= 70:
        risk_label = "High"
    elif result["risk_score"] >= 40:
        risk_label = "Medium"

    return templates.TemplateResponse(
        "result.html",
        {
            "request": request,
            "risk_score": result["risk_score"],
            "risk_label": risk_label,
            "flags": result["flags"],
            "summary": result["summary"],
            "recommended_action": result["recommended_action"],
            "extracted_text": extracted_text,
            "confidence": result["confidence"],
            "degraded": result.get("degraded", False),
            "request_id": request_id,
            "relationship_type": relationship_type,
            "phase": result.get("phase", "NONE"),
            "vie_action": result.get("vie_action", "NONE"),
            "active_combos": result.get("active_combos", []),
            "evidence": result.get("evidence", {}),
            "positive_signals": result.get("positive_signals", []),
              "interest_score": result.get("interest_score"),
              "interest_label": result.get("interest_label"),
        },
    )

