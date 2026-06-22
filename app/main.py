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
from app.db import init_db, log_feedback

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


STATS_SECRET = os.environ.get("STATS_SECRET", "")


@app.get("/audit/stats")
async def audit_stats(request: Request):
    # Require secret header if STATS_SECRET env var is set.
    if STATS_SECRET:
        if request.headers.get("x-stats-secret") != STATS_SECRET:
            raise HTTPException(status_code=403, detail="Forbidden")
    # Query Postgres directly — consolidate into single round trip.
    # Fallback to session stats if DB is unavailable.
    db_url = os.environ.get("DATABASE_URL", "")
    if db_url:
        conn = None
        try:
            import psycopg2
            conn = psycopg2.connect(db_url)
            cur = conn.cursor()
            cur.execute("""
                SELECT
                    COUNT(*)                                                      AS total,
                    COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '24h')  AS last_24h,
                    COUNT(*) FILTER (WHERE degraded = FALSE)                      AS clean,
                    AVG(risk_score)::int                                           AS avg_risk,
                    MAX(risk_score)                                                AS max_risk
                FROM analyses;
            """)
            row = cur.fetchone()
            total, last_24h, clean, avg_risk, max_risk = (
                int(row[0] or 0), int(row[1] or 0), int(row[2] or 0),
                int(row[3] or 0), int(row[4] or 0),
            )
            cur.close()
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
        finally:
            if conn:
                conn.close()
    return get_session_stats()


@app.post("/analyze-screenshots")
async def analyze_screenshots(
    request: Request,
    files: List[UploadFile] = File(...),
    relationship_type: str = Form("stranger"),
    context_note: str = Form(""),
    requested_mode: str = Form("risk"),
    analysis_mode: str = Form("standard"),
    conversation_id: str = Form(""),
    continue_last: str = Form("false"),
):
    request_id = str(uuid.uuid4())
    ts = datetime.now(timezone.utc).isoformat()
    timestamp_start = time.time()

    # Extract UTM params from query string — passed through to DB log for attribution.
    # The frontend must preserve these on the form POST (via hidden fields or JS).
    utm_source   = request.query_params.get("utm_source", "")
    utm_medium   = request.query_params.get("utm_medium", "")
    utm_campaign = request.query_params.get("utm_campaign", "")

    # --- Phase 1 continuity: resolve conversation + fetch prior context ---
    # Fails closed: any DB issue leaves prior_context empty and continuity off.
    _continue = str(continue_last).lower() in ("true", "1", "yes", "on")
    prior_context = ""
    continuity_active = False
    continuity_degraded = False
    conv_meta = {"conversation_id": conversation_id or "", "batch_count": 0, "is_new": True}
    try:
        from app.db import get_or_create_conversation, get_accumulated_context
        conv_meta = get_or_create_conversation(
            conversation_id=conversation_id,
            relationship_type=relationship_type,
        )
        if _continue and conversation_id:
            prior_context = get_accumulated_context(conversation_id, char_cap=6000)
            continuity_active = bool(prior_context)
    except Exception as _conv_err:
        logger.warning(f"[{request_id}] continuity setup failed: {_conv_err}")
        continuity_degraded = True
        conv_meta = {"conversation_id": conversation_id or str(uuid.uuid4()), "batch_count": 0, "is_new": True}

    logger.info(f"[{request_id}] Received {len(files)} file(s), mode={requested_mode} at {ts} "
                f"utm_source={utm_source or 'none'}")

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
        err_str = str(e)
        # [OCR-3] Quality gate fires when mean word confidence is too low — this is a
        # recoverable user-side issue (dark screenshot, heavy redaction, low resolution),
        # not a system failure. Return 422 with actionable guidance instead of 503.
        is_quality_gate = "OCR quality too low" in err_str
        logger.error(f"[{request_id}] OCR {'quality gate' if is_quality_gate else 'failure'}: {err_str}")
        write_audit_record(
            request_id=request_id,
            timestamp_start=timestamp_start,
            image_count=len(files),
            ocr_char_count=0,
            result={},
            degraded=True,
            error=f"OCR {'quality_gate' if is_quality_gate else 'failure'}: {err_str}",
        )
        if is_quality_gate:
            return JSONResponse(
                status_code=422,
                content={
                    "request_id": request_id,
                    "timestamp": ts,
                    "error": "low_ocr_quality",
                    "message": (
                        "We had trouble reading your screenshot clearly. "
                        "Try cropping just the message thread, increasing screen brightness, "
                        "or taking the screenshot in light mode before uploading."
                    ),
                    "degraded": True,
                    "degradation_state": DegradationState.FAIL_CLOSED.value,
                },
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

    # Safety: cap and injection-check context_note before it touches the LLM prompt.
    context_note = context_note[:500]
    from app.analyzer_combined import _check_prompt_injection
    _cn_injected, _cn_match = _check_prompt_injection(context_note)
    if _cn_injected:
        logger.warning(f"[{request_id}] Prompt injection in context_note. Matched: {_cn_match!r}. Clearing.")
        context_note = ""

    # --- Analysis ---
    analysis_error: str | None = None

    try:
        _analysis_input = (prior_context + "\n\n" + extracted_text)[-6000:] if prior_context else extracted_text
        analysis = analyze_text(
            _analysis_input,
            relationship_type=relationship_type,
            context_note=context_note,
            conversation_id=conv_meta["conversation_id"] or None,
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

    payload["analysis_mode"] = analysis_mode

    # --- Phase 1 continuity: save this batch frozen + attach continuity fields ---
    # The per-batch score is written once and never updated by future visits.
    try:
        from app.db import save_batch, get_conversation_batches
        _batch_num = save_batch(
            conversation_id=conv_meta["conversation_id"],
            request_id=request_id,
            ocr_text=extracted_text,
            risk_score=payload.get("risk_score"),
            risk_level=payload.get("risk_level"),
            primary_label=payload.get("primary_label"),
        )
        payload["conversation_id"] = conv_meta["conversation_id"]
        payload["batch_number"] = _batch_num
        payload["prior_batches"] = get_conversation_batches(conv_meta["conversation_id"])
        payload["continuity_active"] = continuity_active
        payload["continuity_degraded"] = continuity_degraded
    except Exception as _save_err:
        logger.warning(f"[{request_id}] batch save failed: {_save_err}")
        payload["continuity_degraded"] = True

    # --- DB log ---
    try:
        from app.db import log_analysis
        log_analysis(
            payload,
            conversation_text=extracted_text,
            utm_source=utm_source,
            utm_medium=utm_medium,
            utm_campaign=utm_campaign,
            analysis_mode=analysis_mode,
        )
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
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {"status": "error", "detail": "ANTHROPIC_API_KEY not set"}
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=32,
            messages=[{"role": "user", "content": "Reply with the word OK only."}],
        )
        return {"status": "ok", "response": msg.content[0].text}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@app.post("/feedback")
async def feedback(request: Request):
    try:
        body = await request.json()
        request_id = str(body.get("request_id", ""))
        accurate = body.get("accurate")
        note = str(body.get("note", ""))
        if request_id and accurate is not None:
            log_feedback(request_id, bool(accurate), note)
            logger.info(f"[feedback] request_id={request_id} accurate={accurate}")
        return JSONResponse({"status": "ok"})
    except Exception as e:
        logger.warning(f"[feedback] parse error: {e}")
        return JSONResponse({"status": "ok"})


@app.post("/log-session")
async def log_session(request: Request):
    try:
        await request.json()
        return JSONResponse({"status": "ok"})
    except Exception:
        return JSONResponse({"status": "ok"})






