from fastapi import APIRouter, UploadFile, File, Form, HTTPException
import tempfile
import os
import logging

from app.api import analyze_image, analyze_text

logger = logging.getLogger("vibelenz.routes")
router = APIRouter()


@router.get("/health")
async def health_check():
    return {"status": "ok", "service": "VibeLenz API"}


@router.post("/analyze/image")
async def analyze_from_image(file: UploadFile = File(...)):
    """
    Accept a screenshot of a conversation (PNG/JPG).
    Runs OCR → turn parser → behavior + relationship_dynamics → combined → interpreter → AnalysisResponse.
    """
    if file.content_type not in ("image/png", "image/jpeg", "image/jpg", "image/webp"):
        raise HTTPException(status_code=415, detail="Unsupported file type. Send PNG, JPG, or WEBP.")

    # Initialize tmp_path before the try block so the finally clause never
    # raises NameError if the NamedTemporaryFile call itself fails.
    tmp_path: str | None = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.filename or "")[-1]) as tmp:
            contents = await file.read()
            tmp.write(contents)
            tmp_path = tmp.name

        logger.info("analyze_from_image: received %s (%d bytes)", file.filename, len(contents))
        result = await analyze_image(tmp_path)
        return result

    except HTTPException:
        raise

    except Exception as e:
        logger.error("analyze_from_image unhandled: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


@router.post("/analyze/text")
async def analyze_from_text(conversation: str = Form(...)):
    """
    Accept raw conversation text (pre-parsed or plain turns).
    Runs turn parser → behavior + relationship_dynamics → combined → interpreter → AnalysisResponse.
    """
    if not conversation or not conversation.strip():
        raise HTTPException(status_code=400, detail="Conversation text cannot be empty.")

    try:
        logger.info("analyze_from_text: %d chars received", len(conversation))
        result = await analyze_text(conversation.strip())
        return result

    except HTTPException:
        raise

    except Exception as e:
        logger.error("analyze_from_text unhandled: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

