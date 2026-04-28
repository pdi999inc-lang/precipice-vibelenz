from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.analyzer_combined import analyze_text, analyze_turns
from app.interpreter import interpret_analysis
from app.ocr import extract_text_from_images
from app.degradation import assess_degradation, apply_degradation, DegradationState
from app.audit import write_audit_record, get_session_stats
from app.db import init_db

logger = logging.getLogger("vibelenz.main")

app = FastAPI(title="VibeLenz")


@app.on_event("startup")
async def startup_event():
    """Initialize DB schema on every startup. Idempotent — safe to run repeatedly."""
    init_db()

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

MAX_FILES = 10
MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB hard cap per file
ALLOWED_TYPES = {"image/png", "image/jpeg", "image/jpg"}
# Magic byte prefixes for PNG and JPEG — validated after read, before OCR.
IMAGE_MAGIC = (b"\x89PNG", b"\xff\xd8\xff")
# PATCH-003: Hard minimum OCR char threshold.
# Below this = image unreadable. Analysis must not run on noise or placeholder text.
MIN_OCR_CHARS = 50


def _simple_page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"<html><head><title>{title}</title></head><body><h1>{title}</h1><p>{body}</p></body></html>"
    )


def _risk_label_from_score(score: int) -> str:
    if score >= 70:
        return "High"
    if score >= 40:
        return "Medium"
    return "Low"


def _build_response_payload(
    request_id: str,
    ts: str,
    extracted_text: str,
    analysis: dict,
    narrative: dict,
    turn_analysis: dict,
) -> dict:
    payload = dict(analysis)
    payload.update(narrative)
    payload["request_id"] = request_id
    payload["timestamp"] = ts
    payload["extracted_text"] = extracted_text
    # PATCH-003: Use .get() with safe defaults.
    # KeyError here would break all responses — defensive defaults protect every path.
    payload["summary"] = narrative.get("diagnosis", "Analysis complete.")
    payload["recommended_action"] = narrative.get("practical_next_steps", "Stay observant.")
    payload["risk_label"] = payload.get("risk_label") or _risk_label_from_score(
        int(payload.get("final_risk_score", payload.get("risk_score", 0)))
    )
    payload["turn_analysis"] = turn_analysis
    # PATCH-003: Guarantee all template-required keys exist.
    # Interpreter always sets these, but degraded or partial paths may not.
    payload.setdefault("mode_title", "Analysis")
    payload.setdefault("mode_tagline", "")
    payload.setdefault("presentation_mode", "risk")
    payload.setdefault("accountability", "")
    payload.setdefault("alternative_explanations", [])
    payload.setdefault("key_dampeners", [])
    payload.setdefault("social_tone", "")
    payload.setdefault("interest_summary", "")
    payload.setdefault("human_label", "")
    payload.setdefault("mode_override_note", "")
    payload.setdefault("degraded", False)
    payload.setdefault("degradation_state", "NOMINAL")
    payload.setdefault("degradation_reasons", [])
    # C5: Non-blocking schema validation — detects field drift without blocking responses.
    # Any mismatch is logged as a warning; the payload is still returned to the caller.
    try:
        from app.schemas import AnalysisResponse
        AnalysisResponse(**payload)
    except Exception as _schema_err:
        logger.warning(f"[schema_drift] AnalysisResponse validation failed: {_schema_err}")
    return payload


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    index_file = TEMPLATES_DIR / "index.html"
    if index_file.exists():
        return templates.TemplateResponse("index.html", {"request": request})
    return _simple_page("VibeLenz", "Home page template not found.")


@app.get("/pitch", response_class=HTMLResponse)
async def pitch(request: Request):
    pitch_file = TEMPLATES_DIR / "pitch.html"
    if pitch_file.exists():
        return templates.TemplateResponse("pitch.html", {"request": request})
    return _simple_page("Pitch", "Pitch page template not found.")


@app.get("/about", response_class=HTMLResponse)
async def about(request: Request):
    about_file = TEMPLATES_DIR / "about.html"
    if about_file.exists():
        return templates.TemplateResponse("about.html", {"request": request})
    return _simple_page("About", "About page template not found.")


@app.get("/static/og-image.svg")
async def og_image():
    target = STATIC_DIR / "og-image.svg"
    if target.exists():
        return FileResponse(str(target), media_type="image/svg+xml")
    raise HTTPException(status_code=404, detail="og-image.svg not found")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/audit/stats")
async def audit_stats():
    # H5: Query Postgres directly — get_session_stats() reads /tmp/ which resets on container restart.
    # Fallback to session stats if DB is unavailable.
    db_url = os.environ.get("DATABASE_URL", "")
    if db_url:
        try:
            import psycopg2
            conn = psycopg2.connect(db_url)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM analyses;")
            total = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM analyses WHERE created_at > NOW() - INTERVAL '24 hours';")
            last_24h = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM analyses WHERE degraded = FALSE;")
            clean = cur.fetchone()[0]
            cur.execute("SELECT AVG(risk_score)::int, MAX(risk_score) FROM analyses;")
            row = cur.fetchone()
            avg_risk = int(row[0] or 0)
            max_risk = int(row[1] or 0)
            cur.close()
            conn.close()
            return {
                "status": "ok",
                "source": "postgres",
                "governance_gate": {
                    "current": total,
                    "target": 200,
                    "clean_reads": clean,
                    "remaining": max(0, 200 - total),
                },
                "total_analyses": total,
                "last_24h": last_24h,
                "avg_risk_score": avg_risk,
                "max_risk_score": max_risk,
            }
        except Exception as _db_err:
            logger.warning(f"DB stats query failed, falling back to session stats: {_db_err}")
    return get_session_stats()


@app.post("/analyze-screenshots")
async def analyze_screenshots(
    request: Request,
    files: List[UploadFile] = File(...),
    relationship_type: str = Form("stranger"),
    context_note: str = "",
    requested_mode: str = Form("risk"),
):
    request_id = str(uuid.uuid4())
    ts = datetime.now(timezone.utc).isoformat()
    timestamp_start = time.time()

    logger.info(f"[{request_id}] Received {len(files)} file(s), mode={requested_mode} at {ts}")

    if len(files) > MAX_FILES:
        raise HTTPException(
            status_code=422,
            detail=f"Maximum {MAX_FILES} files allowed. Received {len(files)}.",
        )

    allowed_exts = {".png", ".jpg", ".jpeg"}

    for f in files:
        filename = (f.filename or "").lower()
        ext = os.path.splitext(filename)[1]
        content_type = (f.content_type or "").lower()

        allowed_by_type = content_type in ALLOWED_TYPES
        allowed_octet_stream_image = (
            content_type == "application/octet-stream" and ext in allowed_exts
        )

        if not (allowed_by_type or allowed_octet_stream_image):
            raise HTTPException(
                status_code=422,
                detail=f"Unsupported file type: {content_type}. Allowed: png, jpg, jpeg.",
            )

    # --- OCR ---
    ocr_char_count = 0

    try:
        image_bytes_list = [await f.read() for f in files]

        # Safety invariant: enforce file size cap and magic byte integrity.
        # Fail closed — reject before OCR if either check fails.
        for idx, img_bytes in enumerate(image_bytes_list):
            if len(img_bytes) > MAX_FILE_BYTES:
                raise HTTPException(
                    status_code=422,
                    detail=f"File {idx + 1} exceeds 10 MB limit. Reduce file size and retry.",
                )
            if not img_bytes.startswith(IMAGE_MAGIC):
                raise HTTPException(
                    status_code=422,
                    detail=f"File {idx + 1} failed format validation. Only PNG and JPEG are accepted.",
                )
        text_chunks = []
        for img_bytes in image_bytes_list:
            chunk = extract_text_from_images([img_bytes])
            text_chunks.append(chunk)
        extracted_text = "\n\n".join(t for t in text_chunks if t.strip())
        ocr_char_count = len(extracted_text)
    except Exception as e:
        logger.error(f"[{request_id}] OCR failure: {e}")
        write_audit_record(
            request_id=request_id,
            timestamp_start=timestamp_start,
            image_count=len(files),
            ocr_char_count=0,
            result={},
            degraded=True,
            error=f"OCR failure: {e}",
        )
        return JSONResponse(
            status_code=503,
            content={
                "error": "fail_closed",
                "reason": "OCR processing failed. System blocked.",
                "request_id": request_id,
                "degradation_state": DegradationState.FAIL_CLOSED.value,
            },
        )

    # PATCH-003: Hard OCR guard.
    # If image yields fewer than MIN_OCR_CHARS, it is unreadable.
    # Return an explicit, honest response. Do NOT analyze noise or placeholder text.
    # A result with no text is not a clean read and must not be logged as one.
    if ocr_char_count < MIN_OCR_CHARS:
        logger.warning(
            f"[{request_id}] OCR returned {ocr_char_count} chars -- below MIN_OCR_CHARS={MIN_OCR_CHARS}. "
            f"Returning insufficient-data response. No analysis run."
        )
        write_audit_record(
            request_id=request_id,
            timestamp_start=timestamp_start,
            image_count=len(files),
            ocr_char_count=ocr_char_count,
            result={},
            degraded=True,
            error=f"OCR insufficient: {ocr_char_count} chars < {MIN_OCR_CHARS} minimum",
        )
        return JSONResponse(
            status_code=422,
            content={
                "request_id": request_id,
                "timestamp": ts,
                "error": "insufficient_ocr_data",
                "message": (
                    "We could not read enough text from your screenshot. "
                    "Try uploading a clearer image with visible conversation text."
                ),
                "ocr_char_count": ocr_char_count,
                "min_required": MIN_OCR_CHARS,
                "degraded": True,
                "degradation_state": DegradationState.FAIL_CLOSED.value,
            },
        )

    # --- Analysis ---
    analysis_error: str | None = None

    try:
        analysis = analyze_text(
            extracted_text,
            relationship_type=relationship_type,
            context_note=context_note,
        )
        narrative = interpret_analysis(
            analysis,
            extracted_text=extracted_text,
            requested_mode=requested_mode,
            relationship_type=relationship_type,
            use_llm=True,
        )
        turn_analysis = analyze_turns(
            text_chunks=[t for t in text_chunks if t.strip()],
            relationship_type=relationship_type,
        )
    except Exception as e:
        analysis_error = str(e)
        logger.error(f"[{request_id}] Analysis failure: {e}")
        write_audit_record(
            request_id=request_id,
            timestamp_start=timestamp_start,
            image_count=len(files),
            ocr_char_count=ocr_char_count,
            result={},
            degraded=True,
            error=f"Analysis failure: {e}",
        )
        return JSONResponse(
            status_code=503,
            content={
                "error": "fail_closed",
                "reason": "Analysis engine failed. System blocked.",
                "request_id": request_id,
                "degradation_state": DegradationState.FAIL_CLOSED.value,
            },
        )

    # --- Degradation assessment ---
    processing_time_ms = int((time.time() - timestamp_start) * 1000)
    confidence = float(analysis.get("confidence", 0.5))

    assessment = assess_degradation(
        ocr_char_count=ocr_char_count,
        confidence=confidence,
        processing_time_ms=processing_time_ms,
        api_error=analysis_error,
        result_degraded=bool(analysis.get("degraded", False)),
    )

    if assessment.should_block:
        logger.error(f"[{request_id}] FAIL_CLOSED triggered — reasons: {assessment.reasons}")
        write_audit_record(
            request_id=request_id,
            timestamp_start=timestamp_start,
            image_count=len(files),
            ocr_char_count=ocr_char_count,
            result=analysis,
            degraded=True,
            error=f"FAIL_CLOSED: {assessment.reasons}",
        )
        return JSONResponse(
            status_code=503,
            content={
                "error": "fail_closed",
                "reason": "System integrity check failed. Analysis blocked.",
                "degradation_state": DegradationState.FAIL_CLOSED.value,
                "degradation_reasons": assessment.reasons,
                "request_id": request_id,
            },
        )

    # Apply soft/hard degradation penalties
    analysis = apply_degradation(analysis, assessment)

    # --- Build payload ---
    payload = _build_response_payload(
        request_id=request_id,
        ts=ts,
        extracted_text=extracted_text,
        analysis=analysis,
        narrative=narrative,
        turn_analysis=turn_analysis,
    )

    # --- DB log ---
    try:
        from app.db import log_analysis
        log_analysis(payload, conversation_text=extracted_text)
    except Exception as _db_err:
        logger.warning(f"DB log skipped: {_db_err}")

    # --- Audit record ---
    write_audit_record(
        request_id=request_id,
        timestamp_start=timestamp_start,
        image_count=len(files),
        ocr_char_count=ocr_char_count,
        result=payload,
        degraded=bool(payload.get("degraded", False)),
    )

    logger.info(
        f"[{request_id}] Risk={payload.get('risk_score')} Lane={payload.get('lane')} "
        f"Mode={requested_mode} Turns={turn_analysis.get('turn_count', 0)} "
        f"Arc={turn_analysis.get('arc', 'n/a')} "
        f"Degraded={payload.get('degraded', False)} DegState={assessment.state.value} "
        f"TookMs={processing_time_ms}"
    )

    accept = request.headers.get("accept", "")
    if "application/json" in accept or "text/html" not in accept:
        return JSONResponse(content=payload)

    template_payload = dict(payload)
    template_payload["request"] = request
    template_payload.setdefault("final_risk_score", template_payload.get("risk_score", 0))

    result_file = TEMPLATES_DIR / "result.html"
    if result_file.exists():
        return templates.TemplateResponse("result.html", template_payload)

    return _simple_page("VibeLenz Result", payload.get("diagnosis", "Analysis complete."))


@app.get("/diag/llm")
async def diag_llm():
    import anthropic as _anthropic
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"status": "error", "detail": "ANTHROPIC_API_KEY not set"}
    try:
        client = _anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            messages=[{"role": "user", "content": "hi"}],
        )
        return {"status": "ok", "response": msg.content[0].text}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@app.post("/feedback")
async def feedback(request: Request):
    try:
        body = await request.json()
        request_id = body.get("request_id", "unknown")
        rating = body.get("rating", "unknown")
        note = body.get("note", "")
        accurate = str(rating).lower() in {"yes", "accurate", "true", "1", "thumbs_up"}
        try:
            from app.db import log_feedback
            log_feedback(request_id=request_id, accurate=accurate, note=note)
        except Exception as db_err:
            logger.warning(f"Feedback DB log skipped: {db_err}")
        return JSONResponse({"status": "ok"})
    except Exception as e:
        logger.error(f"Feedback endpoint error: {e}")
        return JSONResponse({"status": "ok"})




