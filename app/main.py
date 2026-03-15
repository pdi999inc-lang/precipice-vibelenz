import logging
import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.analyzer import analyze_text
from app.audit import write_audit_record
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

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

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
        result = analyze_text(extracted_text)
    except Exception as e:
        logger.error(f"[{request_id}] Analysis failure: {e}")
        raise HTTPException(status_code=503, detail="Analysis engine failed. System blocked.")

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
            "phase": result.get("phase", "NONE"),
            "vie_action": result.get("vie_action", "NONE"),
            "active_combos": result.get("active_combos", []),
            "evidence": result.get("evidence", {}),
        },
    )
