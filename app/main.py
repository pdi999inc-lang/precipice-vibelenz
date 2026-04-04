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

from app.api import analyze_image as _vie_analyze_image
from app.api import analyze_text as _vie_analyze_text
from app.analyzer import analyze_turns
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


def _ocr_image_bytes(img_bytes: bytes, extension: str = ".jpg") -> str:
    from app.ocr import extract_text_from_image
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=extension) as tmp:
            tmp.write(img_bytes)
            tmp_path = tmp.name
        return extract_text_from_image(tmp_path)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


def _flatten_vie_response(response, request_id, ts, extracted_text, turn_analysis, requested_mode):
    if hasattr(response, "model_dump"):
        base = response.model_dump()
    else:
        base = dict(response) if isinstance(response, dict) else {}

    behavior = base.get("behavior") or {}
    if hasattr(behavior, "model_dump"):
        behavior = behavior.model_dump()
    elif not isinstance(behavior, dict):
        behavior = {}

    dynamics = base.get("dynamics") or {}
    if hasattr(dynamics, "model_dump"):
        dynamics = dynamics.model_dump()
    elif not isinstance(dynamics, dict):
        dynamics = {}

    payload = {}
    payload.update(dynamics)
    payload.update(behavior)
    payload.update(base)

    if base.get("status") == "error":
        payload.setdefault("diagnosis", base.get("error", "Analysis failed."))
        payload.setdefault("practical_next_steps", "Please try again.")
        payload.setdefault("presentation_mode", "risk")
        payload.setdefault("mode_title", "Risk Analysis")
        payload.setdefault("mode_tagline", "")
        payload.setdefault("human_label", "error")
        payload.setdefault("reasoning", "")
        payload.setdefault("accountability", "")
        payload.setdefault("social_tone", "")
        payload.setdefault("interest_summary", "")
        payload.setdefault("mode_override_note", "")
        payload.setdefault("requested_mode", requested_mode)

    payload["request_id"] = request_id
    payload["timestamp"] = ts
    payload["extracted_text"] = extracted_text
    payload["turn_analysis"] = turn_analysis
    payload["requested_mode"] = requested_mode
    payload["summary"] = payload.get("diagnosis", "")
    payload["recommended_action"] = payload.get("practical_next_steps", "")
    payload["risk_label"] = payload.get("risk_label") or _risk_label_from_score(
        int(payload.get("risk_score", 0))
    )
    return payload


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    if (TEMPLATES_DIR / "index.html").exists():
        return templates.TemplateResponse("index.html", {"request": request})
    return _simple_page("VibeLenz", "Home page template not found.")


@app.get("/pitch", response_class=HTMLResponse)
async def pitch(request: Request):
    if (TEMPLATES_DIR / "pitch.html").exists():
        return templates.TemplateResponse("pitch.html", {"request": request})
    return _simple_page("Pitch", "Pitch page template not found.")


@app.get("/about", response_class=HTMLResponse)
async def about(request: Request):
    if (TEMPLATES_DIR / "about.html").exists():
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

    logger.info("[%s] Received %d file(s), mode=%s", request_id, len(files), requested_mode)

    if len(files) > MAX_FILES:
        raise HTTPException(status_code=422, detail=f"Maximum {MAX_FILES} files allowed.")

    allowed_exts = {".png", ".jpg", ".jpeg"}
    for f in files:
        filename = (f.filename or "").lower()
        ext = os.path.splitext(filename)[1]
        content_type = (f.content_type or "").lower()
        if not (content_type in ALLOWED_TYPES or (content_type == "application/octet-stream" and ext in allowed_exts)):
            raise HTTPException(status_code=422, detail=f"Unsupported file type: {content_type}.")

    try:
        uploaded = []
        for f in files:
            img_bytes = await f.read()
            ext = os.path.splitext((f.filename or "image.jpg").lower())[1] or ".jpg"
            uploaded.append((img_bytes, ext))
    except Exception as e:
        logger.error("[%s] File read failure: %s", request_id, e)
        raise HTTPException(status_code=422, detail="Could not read uploaded file(s).")

    try:
        text_chunks = [_ocr_image_bytes(b, extension=e) for b, e in uploaded]
        extracted_text = "\n\n".join(t for t in text_chunks if t.strip())
    except Exception as e:
        logger.error("[%s] OCR failure: %s", request_id, e)
        raise HTTPException(status_code=503, detail="OCR processing failed.")

    if not extracted_text.strip():
        extracted_text = "[No readable text detected in uploaded images]"

    try:
        vie_response = await _vie_analyze_text(extracted_text)
    except Exception as e:
        logger.error("[%s] VIE analysis failure: %s", request_id, e)
        raise HTTPException(status_code=503, detail="Analysis engine failed.")

    try:
        turn_analysis = analyze_turns(
            text_chunks=[t for t in text_chunks if t.strip()],
            relationship_type=relationship_type,
        )
    except Exception as e:
        logger.warning("[%s] Turn analysis failed (non-fatal): %s", request_id, e)
        turn_analysis = {"turn_count": 0, "arc": "n/a", "turns": []}

    payload = _flatten_vie_response(
        response=vie_response,
        request_id=request_id,
        ts=ts,
        extracted_text=extracted_text,
        turn_analysis=turn_analysis,
        requested_mode=requested_mode,
    )

    logger.info(
        "[%s] Risk=%s Lane=%s Degraded=%s TookMs=%d",
        request_id,
        payload.get("risk_score"),
        payload.get("lane"),
        payload.get("degraded", False),
        int((time.time() - timestamp_start) * 1000),
    )

    accept = request.headers.get("accept", "")
    if "application/json" in accept or "text/html" not in accept:
        return JSONResponse(content=payload)

    template_payload = dict(payload)
    template_payload["request"] = request

    if (TEMPLATES_DIR / "result.html").exists():
        return templates.TemplateResponse("result.html", template_payload)

    return _simple_page("VibeLenz Result", payload.get("diagnosis", "Analysis complete."))

