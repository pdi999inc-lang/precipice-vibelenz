from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.api import analyze_text as api_analyze_text
from app.audit import get_session_stats, write_audit_record
from app.db import init_db, log_analysis, log_feedback
from app.ocr import extract_text_from_images

logger = logging.getLogger("vibelenz.main")

app = FastAPI(title="VibeLenz")

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

MAX_FILES = 10
ALLOWED_TYPES = {"image/png", "image/jpeg", "image/jpg"}


@app.on_event("startup")
async def startup_event() -> None:
    init_db()


def _simple_page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"<html><head><title>{title}</title></head>"
        f"<body><h1>{title}</h1><p>{body}</p></body></html>"
    )


def _risk_label_from_score(score: int) -> str:
    if score >= 70:
        return "High"
    if score >= 40:
        return "Medium"
    return "Low"


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    index_file = TEMPLATES_DIR / "index.html"
    if index_file.exists():
        return templates.TemplateResponse("index.html", {"request": request})
    return _simple_page("VibeLenz", "Home page template not found.")


@app.get("/pitch", response_class=HTMLResponse)
async def pitch(request: Request) -> HTMLResponse:
    pitch_file = TEMPLATES_DIR / "pitch.html"
    if pitch_file.exists():
        return templates.TemplateResponse("pitch.html", {"request": request})
    return _simple_page("Pitch", "Pitch page template not found.")


@app.get("/about", response_class=HTMLResponse)
async def about(request: Request) -> HTMLResponse:
    about_file = TEMPLATES_DIR / "about.html"
    if about_file.exists():
        return templates.TemplateResponse("about.html", {"request": request})
    return _simple_page("About", "About page template not found.")


@app.get("/static/og-image.svg")
async def og_image() -> FileResponse:
    target = STATIC_DIR / "og-image.svg"
    if target.exists():
        return FileResponse(str(target), media_type="image/svg+xml")
    raise HTTPException(status_code=404, detail="og-image.svg not found")


@app.post("/feedback")
async def feedback(request: Request) -> HTMLResponse:
    form = await request.form()
    request_id = str(form.get("request_id", ""))
    accurate = form.get("accurate", "") == "yes"
    note = str(form.get("note", ""))
    if request_id:
        try:
            log_feedback(request_id, accurate, note)
        except Exception:
            pass
    return HTMLResponse("<script>history.back()</script>")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/audit/stats")
async def audit_stats() -> dict:
    return get_session_stats()


@app.post("/analyze-screenshots")
async def analyze_screenshots(
    request: Request,
    files: List[UploadFile] = File(...),
    relationship_type: str = "stranger",
    other_gender: str = "unknown",
    context_note: str = "",
    requested_mode: str = "risk",
):
    request_id = str(uuid.uuid4())
    ts = datetime.now(timezone.utc).isoformat()
    timestamp_start = time.time()

    logger.info(
        f"[{request_id}] Received {len(files)} file(s), "
        f"mode={requested_mode} gender={other_gender} at {ts}"
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

    try:
        image_bytes_list = [await f.read() for f in files]
        text_chunks = []
        for img_bytes in image_bytes_list:
            chunk = extract_text_from_images([img_bytes])
            text_chunks.append(chunk)
        extracted_text = "\n\n".join(t for t in text_chunks if t.strip())
    except Exception as e:
        logger.error(f"[{request_id}] OCR failure: {e}")
        raise HTTPException(status_code=503, detail="OCR processing failed. System blocked.")

    if not extracted_text.strip():
        extracted_text = "[No readable text detected in uploaded images]"

    try:
        response = await api_analyze_text(
            raw_text=extracted_text,
            relationship_type=relationship_type,
            other_gender=other_gender,
            context_note=context_note,
            requested_mode=requested_mode,
        )
        if hasattr(response, "model_dump"):
            payload = response.model_dump()
        else:
            payload = dict(response)
    except Exception as e:
        logger.error(f"[{request_id}] Analysis failure: {e}")
        raise HTTPException(status_code=503, detail="Analysis engine failed. System blocked.")

    payload["request_id"] = request_id
    payload["timestamp"] = ts
    payload["extracted_text"] = extracted_text
    payload["risk_label"] = payload.get("risk_label") or _risk_label_from_score(
        int(payload.get("risk_score", 0))
    )

    try:
        write_audit_record(
            request_id=request_id,
            timestamp_start=timestamp_start,
            image_count=len(files),
            ocr_char_count=len(extracted_text),
            result=payload,
            degraded=payload.get("degraded", False),
        )
    except Exception as e:
        logger.warning(f"[{request_id}] Audit write failed: {e}")

    try:
        log_analysis(
            {**payload, "relationship_type": relationship_type, "requested_mode": requested_mode},
            conversation_text=extracted_text,
        )
    except Exception as e:
        logger.warning(f"[{request_id}] DB log failed: {e}")

    logger.info(
        f"[{request_id}] Risk={payload.get('risk_score')} Lane={payload.get('lane')} "
        f"Mode={requested_mode} Gender={other_gender} "
        f"Degraded={payload.get('degraded', False)} "
        f"TookMs={int((time.time() - timestamp_start) * 1000)}"
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
