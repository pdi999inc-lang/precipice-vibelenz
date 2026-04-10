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

from app.analyzer_combined import analyze_text, analyze_turns
from app.db import init_db, log_analysis, log_feedback
from app.interpreter import interpret_analysis
from app.ocr import extract_text_from_images
from app.relationship_dynamics import analyze_dynamics

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vibelenz.main")

app = FastAPI(title="VibeLenz")


@app.on_event("startup")
async def startup():
    init_db()


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


def _parse_turns_for_dynamics(text_chunks: List[str], user_side: str) -> List[dict]:
    turns = []
    for chunk in text_chunks:
        if not chunk.strip():
            continue
        lines = [l.strip() for l in chunk.splitlines() if l.strip()]
        for j, line in enumerate(lines):
            if user_side == "right":
                sender = "user" if j % 2 == 1 else "other"
            elif user_side == "left":
                sender = "user" if j % 2 == 0 else "other"
            else:
                sender = "user" if j % 2 == 0 else "other"
            turns.append({
                "turn_id": f"T{len(turns) + 1}",
                "sender": sender,
                "text": line,
            })
    return turns


def _build_response_payload(
    request_id: str,
    ts: str,
    extracted_text: str,
    analysis: dict,
    narrative: dict,
    turn_analysis: dict,
    dynamics: dict,
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
    payload["relationship_dynamics"] = dynamics

    if not payload.get("signal_breakdown"):
        payload["signal_breakdown"] = analysis.get("signal_breakdown", [])

    if not payload.get("human_label"):
        raw = payload.get("primary_label") or payload.get("lane") or "interaction"
        payload["human_label"] = raw.replace("_", " ").lower()

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


@app.post("/feedback")
async def feedback(request: Request):
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
    user_side: str = "unknown",
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

    speaker_map = {
        "right": "The user is the person on the RIGHT side of the conversation (purple/dark bubbles). The other person is on the LEFT (white/gray bubbles).",
        "left": "The user is the person on the LEFT side of the conversation (white/gray bubbles). The other person is on the RIGHT (purple/dark bubbles).",
        "unknown": "Speaker side is unknown. Do not assume which side belongs to the user.",
    }
    speaker_context = speaker_map.get(user_side, speaker_map["unknown"])
    enriched_context = f"{speaker_context} {context_note}".strip()

    try:
        analysis = analyze_text(
            extracted_text,
            relationship_type=relationship_type,
            context_note=enriched_context,
        )
        narrative = interpret_analysis(
            analysis,
            extracted_text=extracted_text,
            requested_mode=requested_mode,
            use_llm=True,
        )
        turn_analysis = analyze_turns(
            text_chunks,
            relationship_type=relationship_type,
        )
        dynamics_turns = _parse_turns_for_dynamics(text_chunks, user_side)
        dynamics = analyze_dynamics(dynamics_turns)

    except Exception as e:
        logger.error(f"[{request_id}] Analysis failure: {e}")
        raise HTTPException(status_code=503, detail="Analysis engine failed. System blocked.")

    payload = _build_response_payload(
        request_id=request_id,
        ts=ts,
        extracted_text=extracted_text,
        analysis=analysis,
        narrative=narrative,
        turn_analysis=turn_analysis,
        dynamics=dynamics,
    )

    try:
        log_analysis(payload, conversation_text=extracted_text)
    except Exception:
        pass

    logger.info(
        f"[{request_id}] Risk={payload.get('risk_score')} Lane={payload.get('lane')} "
        f"Mode={requested_mode} LLM={payload.get('llm_enriched')} "
        f"Dynamics={dynamics.get('momentum_direction')} "
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
