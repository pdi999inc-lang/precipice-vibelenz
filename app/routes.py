from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from typing import Optional
import tempfile
import os
from app.api import analyze_image, analyze_text

router = APIRouter()


@router.get("/health")
async def health_check():
    return {"status": "ok", "service": "VibeLenz API"}


@router.post("/analyze/image")
async def analyze_from_image(file: UploadFile = File(...)):
    """
    Accept a screenshot of a conversation (PNG/JPG/WEBP).
    Runs OCR → turn parser → behavior + relationship_dynamics → verifier → AnalysisResponse.
    """
    if file.content_type not in ("image/png", "image/jpeg", "image/jpg", "image/webp"):
        raise HTTPException(
            status_code=415,
            detail="Unsupported file type. Send PNG, JPG, or WEBP."
        )

    tmp_path = None
    try:
        suffix = os.path.splitext(file.filename)[-1] if file.filename else ".png"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            contents = await file.read()
            tmp.write(contents)
            tmp_path = tmp.name

        result = await analyze_image(tmp_path)
        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


@router.post("/analyze/text")
async def analyze_from_text(conversation: str = Form(...)):
    """
    Accept raw conversation text (pre-parsed or plain turns).
    Runs turn parser → behavior + relationship_dynamics → verifier → AnalysisResponse.
    """
    if not conversation or not conversation.strip():
        raise HTTPException(status_code=400, detail="Conversation text cannot be empty.")

    try:
        result = await analyze_text(conversation.strip())
        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
