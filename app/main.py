from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

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
# Paste-text input path: hard cap on pasted conversation length.
# Mirrors analyzer truncation (8000) with headroom; oversized input fails closed at 422.
MAX_PASTE_CHARS = 15000
# Follow-up Q&A: per-analysis question cap and input bound.
# The cap is enforced server-side from submitted history; it is also the
# natural premium metering point later.
MAX_FOLLOWUP_QUESTIONS = 5
MAX_FOLLOWUP_CHARS = 500


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
        # Sub-brand host routing: any domain containing "purport" serves the
        # PurPort (risk) page as its homepage. Same app, same engine, two brands.
        _host = (request.headers.get("host") or "").lower()
        _mode = "risk" if "purport" in _host else "connection"
        return templates.TemplateResponse("index.html", {"request": request, "page_mode": _mode})
    return _simple_page("VibeLenz", "Home page template not found.")


@app.get("/scam-check", response_class=HTMLResponse)
async def scam_check(request: Request):
    """Dedicated risk-mode page. Same template, same engines — page_mode fixes the mode."""
    index_file = TEMPLATES_DIR / "index.html"
    if index_file.exists():
        return templates.TemplateResponse("index.html", {"request": request, "page_mode": "risk"})
    return _simple_page("Scam Check", "Template not found.")


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
    files: List[UploadFile] = File(default=[]),
    pasted_text: str = Form(""),
    relationship_type: str = Form("stranger"),
    context_note: str = Form(""),
    requested_mode: str = Form("risk"),
    analysis_mode: str = Form("standard"),
    conversation_id: str = Form(""),
    continue_last: str = Form("false"),
    other_gender: str = Form("unknown"),
):
    request_id = str(uuid.uuid4())
    ts = datetime.now(timezone.utc).isoformat()
    timestamp_start = time.time()

    # Extract UTM params from query string — passed through to DB log for attribution.
    # The frontend must preserve these on the form POST (via hidden fields or JS).
    utm_source   = request.query_params.get("utm_source", "")
    utm_medium   = request.query_params.get("utm_medium", "")
    utm_campaign = request.query_params.get("utm_campaign", "")

    # --- Paste-text input path (alternative to screenshots) ---
    # Fail-closed rules: exactly one input type; length bounds enforced before
    # any analysis. Pasted text follows the identical pipeline after this point
    # (injection guard lives in analyze_text; context_note guard unchanged).
    files = files or []
    pasted_text = (pasted_text or "").strip()
    use_paste = bool(pasted_text)
    if use_paste and files:
        raise HTTPException(
            status_code=422,
            detail="Provide screenshots or pasted text, not both.",
        )
    if not use_paste and not files:
        raise HTTPException(
            status_code=422,
            detail="Provide at least one screenshot or paste the conversation text.",
        )
    if use_paste and len(pasted_text) > MAX_PASTE_CHARS:
        raise HTTPException(
            status_code=422,
            detail=f"Pasted text exceeds {MAX_PASTE_CHARS} characters. Shorten and retry.",
        )
    if use_paste and len(pasted_text) < MIN_OCR_CHARS:
        # Same honesty rule as the OCR guard: too little text is not a clean read.
        write_audit_record(
            request_id=request_id,
            timestamp_start=timestamp_start,
            image_count=0,
            ocr_char_count=len(pasted_text),
            result={},
            degraded=True,
            error=f"Pasted text insufficient: {len(pasted_text)} chars < {MIN_OCR_CHARS} minimum",
        )
        return JSONResponse(
            status_code=422,
            content={
                "request_id": request_id,
                "timestamp": ts,
                "error": "insufficient_text",
                "message": (
                    "That's not enough conversation to analyze. "
                    "Paste at least a few messages of the exchange."
                ),
                "min_required": MIN_OCR_CHARS,
                "degraded": True,
                "degradation_state": DegradationState.FAIL_CLOSED.value,
            },
        )

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

    # --- Input text acquisition: paste path skips OCR entirely ---
    ocr_char_count = 0

    if use_paste:
        text_chunks = [pasted_text]
        extracted_text = pasted_text
        ocr_char_count = len(extracted_text)

    if not use_paste:
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

    # --- Reply suggestions ---
    try:
        from app.reply_engine import generate_replies
        _reply_data = generate_replies(
            payload=dict(analysis, **narrative),
            extracted_text=extracted_text,
            other_gender=other_gender,
        )
    except Exception as _reply_err:
        logger.warning(f"[{request_id}] reply generation failed: {_reply_err}")
        _reply_data = {"suggested_replies": [], "reply_mode": "error", "replies_suppressed": False, "replies_suppressed_reason": "Reply generation unavailable"}

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
    payload["input_source"] = "paste" if use_paste else "screenshots"
    payload["suggested_replies"] = _reply_data.get("suggested_replies", [])
    payload["reply_mode"] = _reply_data.get("reply_mode", "error")
    payload["replies_suppressed"] = _reply_data.get("replies_suppressed", False)
    payload["replies_suppressed_reason"] = _reply_data.get("replies_suppressed_reason")

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

    # --- Outcome Engine Phase 1: emit + store one falsifiable prediction ---
    # Derived deterministically from existing payload fields — no new engine.
    # Internal-only: accuracy is never computed or shown at write time.
    # Fail-closed: any error skips prediction storage and never blocks the read.
    try:
        from app.db import save_prediction
        _lane_p = str(payload.get("lane", "BENIGN"))
        _iscore = payload.get("interest_score")
        _direction = str((turn_analysis or {}).get("direction", "neutral"))
        if _lane_p in ("FRAUD", "COERCION_RISK"):
            _ptype, _pval, _pwin = "escalation", "pressure_or_ask_continues", 7
        elif isinstance(_iscore, int) and _iscore >= 60 and _direction not in ("worsening", "concerning"):
            _ptype, _pval, _pwin = "engagement", "reply_or_warmth_continues", 3
        elif (isinstance(_iscore, int) and _iscore <= 35) or _direction in ("worsening", "concerning"):
            _ptype, _pval, _pwin = "fade", "contact_slows_or_stops", 7
        else:
            _ptype, _pval, _pwin = "steady", "no_major_shift", 7
        _cid_p = payload.get("conversation_id") or conv_meta.get("conversation_id") or ""
        if _cid_p:
            save_prediction(
                conversation_id=_cid_p,
                request_id=request_id,
                prediction_type=_ptype,
                predicted_value=_pval,
                window_days=_pwin,
            )
            payload["prediction"] = {"type": _ptype, "value": _pval, "window_days": _pwin}
    except Exception as _pred_err:
        logger.warning(f"[{request_id}] prediction store skipped: {_pred_err}")

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
        _json_resp = JSONResponse(content=payload)
        _cid = payload.get("conversation_id")
        if _cid:
            _json_resp.headers["X-Conversation-Id"] = str(_cid)
        return _json_resp

    template_payload = dict(payload)
    template_payload["request"] = request
    template_payload.setdefault("final_risk_score", template_payload.get("risk_score", 0))

    result_file = TEMPLATES_DIR / "result.html"
    if result_file.exists():
        _resp = templates.TemplateResponse("result.html", template_payload)
        _cid = payload.get("conversation_id")
        if _cid:
            _resp.headers["X-Conversation-Id"] = str(_cid)
        return _resp

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


@app.get("/conversation/{conversation_id}/summary")
async def conversation_summary(conversation_id: str):
    """
    Phase 1: return summary of a conversation for the 'Continue last' UI.
    Returns batch_count and the last frozen batch read. Fails closed: on any
    error or missing conversation, returns status=not_found with empty data
    so the frontend can fall back to treating it as a new conversation.
    """
    try:
        from app.db import get_conversation_batches
        batches = get_conversation_batches(conversation_id)
        if not batches:
            return JSONResponse({"status": "not_found", "conversation_id": conversation_id, "batch_count": 0})
        last = batches[-1]
        # Outcome Engine: surface the open prediction so the frontend can ask
        # "what happened since?" on continue-last. Fail-closed to None.
        _open_pred = None
        try:
            from app.db import get_open_prediction
            _open_pred = get_open_prediction(conversation_id)
        except Exception as _op_err:
            logger.warning(f"open prediction fetch skipped: {_op_err}")
        return JSONResponse({
            "status": "ok",
            "conversation_id": conversation_id,
            "batch_count": len(batches),
            "last_batch": last,
            "all_batches": batches,
            "open_prediction": _open_pred,
        })
    except Exception as e:
        logger.warning(f"conversation_summary failed: {e}")
        return JSONResponse({"status": "error", "conversation_id": conversation_id, "batch_count": 0})


@app.post("/outcome")
async def outcome(request: Request):
    """
    Outcome Engine Phase 1: record what actually happened, attached to the
    latest open prediction for the conversation. Raw outcome only — no
    scoring at write time. Fail-closed: always returns ok so the frontend
    never breaks on a capture failure.
    """
    try:
        body = await request.json()
        conversation_id = str(body.get("conversation_id", ""))[:64]
        outcome_val = str(body.get("outcome", ""))[:40]
        _allowed = {"warmed_up", "lukewarm", "went_quiet"}
        if not conversation_id or outcome_val not in _allowed:
            return JSONResponse(status_code=422, content={"error": "invalid_outcome"})
        from app.db import record_outcome
        _recorded = record_outcome(conversation_id, outcome_val, source="continue_last")
        logger.info(f"[outcome] conv={conversation_id} outcome={outcome_val} recorded={_recorded}")
        return JSONResponse({"status": "ok", "recorded": _recorded})
    except Exception as e:
        logger.warning(f"[outcome] error: {e}")
        return JSONResponse({"status": "ok", "recorded": False})


@app.post("/followup")
async def followup(request: Request):
    """
    Post-analysis follow-up Q&A ("Ask about this read").
    Context is loaded server-side by request_id from the analyses table —
    never trusted from the client. Hard limits: MAX_FOLLOWUP_QUESTIONS per
    analysis, MAX_FOLLOWUP_CHARS per question. Injection-guarded. Lane-aware:
    FRAUD/COERCION_RISK analyses get a clinical protective voice; connection
    reads get the casual voice. Fail-closed: any error returns a safe
    'unavailable' message — no unfiltered LLM output, no page breakage.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=422, content={"error": "invalid_json"})

    request_id = str(body.get("request_id", ""))[:64]
    question = str(body.get("question", "")).strip()[:MAX_FOLLOWUP_CHARS]
    history = body.get("history", [])
    if not isinstance(history, list):
        history = []
    if not request_id or not question:
        return JSONResponse(status_code=422, content={"error": "missing_fields"})

    # Server-side cap: count prior user turns from submitted history.
    _prior_user_turns = len([
        h for h in history
        if isinstance(h, dict) and h.get("role") == "user" and str(h.get("content", "")).strip()
    ])
    if _prior_user_turns >= MAX_FOLLOWUP_QUESTIONS:
        return JSONResponse(status_code=429, content={
            "error": "question_limit",
            "message": "That's the limit for this read. Run a fresh analysis as the conversation develops.",
        })

    # Injection guard — same guard as every other text input path.
    from app.analyzer_combined import _check_prompt_injection, _sanitize_prohibited_claims
    _inj, _match = _check_prompt_injection(question)
    if _inj:
        logger.warning(f"[followup:{request_id}] prompt injection blocked: {_match!r}")
        return JSONResponse(status_code=422, content={
            "error": "blocked",
            "message": "That question can't be processed.",
        })

    # Load analysis context server-side.
    row = None
    try:
        from app.db import get_conn
        conn = get_conn()
        if conn:
            try:
                cur = conn.cursor()
                cur.execute("""
                    SELECT risk_score, risk_level, lane, primary_label, presentation_mode,
                           flags, positive_signals, conversation_text, relationship_type
                    FROM analyses WHERE request_id = %s
                """, (request_id,))
                row = cur.fetchone()
                cur.close()
            finally:
                conn.close()
    except Exception as e:
        logger.warning(f"[followup:{request_id}] context load failed: {e}")

    if not row:
        return JSONResponse(status_code=404, content={
            "error": "not_found",
            "message": "This analysis is no longer available for follow-up. Run a fresh one.",
        })

    _risk_score, _risk_level, _lane, _primary_label, _presentation_mode, \
        _flags, _positive_signals, _conversation_text, _relationship_type = row
    _lane = str(_lane or "BENIGN")
    _is_safety = _lane in ("FRAUD", "COERCION_RISK")

    import json as _json
    _context_block = (
        f"ANALYSIS CONTEXT (authoritative — do not contradict it):\n"
        f"Risk score: {_risk_score} ({_risk_level})\n"
        f"Lane: {_lane}\n"
        f"Primary read: {_primary_label}\n"
        f"Mode: {_presentation_mode}\n"
        f"Relationship type: {_relationship_type}\n"
        f"Signals: {_json.dumps(_flags if isinstance(_flags, list) else [])}\n"
        f"Positive signals: {_json.dumps(_positive_signals if isinstance(_positive_signals, list) else [])}\n\n"
        f"THE CONVERSATION THAT WAS ANALYZED:\n{(_conversation_text or '')[:6000]}"
    )

    if _is_safety:
        _fu_system = (
            "You are VibeLenz's follow-up assistant. A safety analysis flagged this conversation "
            "as a fraud or coercion risk. Your job is to answer the user's follow-up questions "
            "clearly and protectively.\n"
            "RULES:\n"
            "- Never soften, walk back, or second-guess the risk finding. If asked 'but could it be fine?', "
            "explain why the flagged pattern matters and what independent verification would look like.\n"
            "- Be clinical, calm, and specific. No slang, no jokes.\n"
            "- Practical protective steps only: verify independently, do not send money or personal "
            "information, involve a trusted person, report to the platform.\n"
            "- Do not provide advice unrelated to this conversation and its risks. Redirect briefly.\n"
            "- Never diagnose people. Describe behavior patterns.\n"
            "- Under 130 words. Plain text only."
        )
    else:
        _fu_system = (
            "You are VibeLenz's follow-up assistant — the group chat's smartest friend, answering "
            "questions about a conversation that was just analyzed.\n"
            "RULES:\n"
            "- Ground every answer ONLY in the analyzed conversation and the analysis context. "
            "Quote or reference specific messages when you can.\n"
            "- Casual, current voice. Contractions. Natural dating vocabulary (vibe, energy, red flag, "
            "mixed signals) where it fits — never forced.\n"
            "- Casual voice, surgical read: stay sharp and specific. Make a call; don't hedge.\n"
            "- If asked something outside this conversation (general life advice, therapy, medical, "
            "legal, other people), say briefly that you only work with this conversation and steer back.\n"
            "- Never diagnose people (narcissist, toxic person) — name behaviors in the messages.\n"
            "- No deception coaching, no manipulation tactics, no messages designed to guilt or coerce.\n"
            "- Under 130 words. Plain text only."
        )

    # Build message list: bounded history + current question.
    _messages = []
    for h in history[-8:]:
        if not isinstance(h, dict):
            continue
        _role = "user" if h.get("role") == "user" else "assistant"
        _content = str(h.get("content", ""))[:MAX_FOLLOWUP_CHARS * 2].strip()
        if _content:
            _messages.append({"role": _role, "content": _content})
    _messages.append({"role": "user", "content": question})

    try:
        import anthropic
        _api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not _api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        _client = anthropic.Anthropic(api_key=_api_key)
        _msg = _client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            system=_fu_system + "\n\n" + _context_block,
            messages=_messages,
        )
        _answer = _msg.content[0].text.strip()
        _answer = _sanitize_prohibited_claims(_answer)
        logger.info(f"[followup:{request_id}] q#{_prior_user_turns + 1} lane={_lane} answered ({len(_answer)} chars)")
        return JSONResponse({
            "answer": _answer,
            "questions_used": _prior_user_turns + 1,
            "questions_limit": MAX_FOLLOWUP_QUESTIONS,
        })
    except Exception as e:
        logger.warning(f"[followup:{request_id}] LLM call failed: {e}")
        return JSONResponse(status_code=503, content={
            "error": "unavailable",
            "message": "Follow-up is unavailable right now — the analysis above still stands.",
        })


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
