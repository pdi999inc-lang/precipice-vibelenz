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
    if file.content_type not in ("image/png","image/jpeg","image/jpg","image/webp"):
        raise HTTPException(status_code=415, detail="Unsupported file type.")
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.filename or "")[-1]) as tmp:
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
    if not conversation or not conversation.strip():
        raise HTTPException(status_code=400, detail="Conversation text cannot be empty.")
    try:
        result = await analyze_text(conversation.strip())
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
