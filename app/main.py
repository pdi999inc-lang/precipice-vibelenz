from __future__ import annotations

import logging
import os
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.analyzer import analyze_text, analyze_turns
from app.interpreter import interpret_analysis
from app.ocr import extract_text_from_image
from app.routes import router as vie_router

logger = logging.getLogger("vibelenz.main")

app = FastAPI(title="VibeLenz")
app.include_router(vie_router, prefix="/v1")

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

MAX_FILES = 10
ALLOWED_TYPES = {"image/png", "image/jpeg", "image/jpg"}


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
    payload["summary"] = narrative["diagnosis"]
    payload["recommended_action"] = narrative["practical_next_steps"]
    payload["risk_label"] = payload.get("risk_label") or _risk_label_from_score(
        int(payload.get("final_risk_score", payload.get("risk_score", 0)))
    )
    payload["turn_analysis"] = turn_analysis
    return payload


def _ocr_image_bytes(img_bytes: bytes, extension: str = ".jpg") -> str:
    """
    Write image bytes to a temp file, run OCR, clean up.
    Returns extracted text string. Raises on OCR failure — caller handles.
    extract_text_from_image expects a file path string, not bytes.
    """
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=extension) as tmp:
            tmp.write(img_bytes)
            tmp_path = tmp.name
        return extract_text_from_image(tmp_path)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


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
    return {"status": "ok", "audit": "rewrite_stub"}


@app.post("/analyze-screenshots")
async def analyze_screenshots(
    request: Request,
    files: List[UploadFile] = File(...),
    relationship_type: str = "stranger",
    context_note: str = "",
    requested_mode: str = "risk",
):
    request_id = str(uuid.uuid4())
    ts = datetime.now(timezone.utc).isoformat()
    timestamp_start = time.time()

    logger.info(
        "[%s] Received %d file(s), mode=%s at %s",
        request_id, len(files), requested_mode, ts,
    )

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

    # Read all files up front so UploadFile handles are consumed once
    try:
        uploaded = []
        for f in files:
            img_bytes = await f.read()
            ext = os.path.splitext((f.filename or "image.jpg").lower())[1] or ".jpg"
            uploaded.append((img_bytes, ext))
    except Exception as e:
        logger.error("[%s] File read failure: %s", request_id, e)
        raise HTTPException(status_code=422, detail="Could not read uploaded file(s).")

    # OCR each image via temp file — extract_text_from_image expects a path string
    try:
        text_chunks = []
        for img_bytes, ext in uploaded:
            chunk = _ocr_image_bytes(img_bytes, extension=ext)
            text_chunks.append(chunk)
        extracted_text = "\n\n".join(t for t in text_chunks if t.strip())
    except Exception as e:
        logger.error("[%s] OCR failure: %s", request_id, e)
        raise HTTPException(status_code=503, detail="OCR processing failed. System blocked.")

    if not extracted_text.strip():
        extracted_text = "[No readable text detected in uploaded images]"

    try:
        analysis = analyze_text(
            extracted_text,
            relationship_type=relationship_type,
            context_note=context_note,
        )
        narrative = interpret_analysis(analysis, requested_mode=requested_mode)

        # Multi-turn analysis — only meaningful when more than one image uploaded
        turn_analysis = analyze_turns(
            text_chunks=[t for t in text_chunks if t.strip()],
            relationship_type=relationship_type,
        )
    except Exception as e:
        logger.error("[%s] Analysis failure: %s", request_id, e)
        raise HTTPException(status_code=503, detail="Analysis engine failed. System blocked.")

    payload = _build_response_payload(
        request_id=request_id,
        ts=ts,
        extracted_text=extracted_text,
        analysis=analysis,
        narrative=narrative,
        turn_analysis=turn_analysis,
    )

    logger.info(
        "[%s] Risk=%s Lane=%s Mode=%s Turns=%d Arc=%s Degraded=%s TookMs=%d",
        request_id,
        payload.get("risk_score"),
        payload.get("lane"),
        requested_mode,
        turn_analysis.get("turn_count", 0),
        turn_analysis.get("arc", "n/a"),
        payload.get("degraded", False),
        int((time.time() - timestamp_start) * 1000),
    )

    accept = request.headers.get("accept", "")
    if "application/json" in accept or "text/html" not in accept:
        return JSONResponse(content=payload)

    template_payload = dict(payload)
    template_payload["request"] = request

    result_file = TEMPLATES_DIR / "result.html"
    if result_file.exists():
        return templates.TemplateResponse("result.html", template_payload)

    return _simple_page("VibeLenz Result", payload.get("diagnosis", "Analysis complete."))



