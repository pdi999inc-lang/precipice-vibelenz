from __future__ import annotations

import os
import tempfile
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.api import analyze_image, analyze_text

router = APIRouter(prefix="/v1")


@router.get("/health")
async def health_check():
    return {"status": "ok", "service": "VibeLenz API"}


@router.post("/analyze/image")
async def analyze_from_image(
    file: UploadFile = File(...),
    relationship_type: str = Form(default="stranger"),
    other_gender: str = Form(default="unknown"),
    context_note: str = Form(default=""),
    requested_mode: str = Form(default="risk"),
):
    if file.content_type not in ("image/png", "image/jpeg", "image/jpg", "image/webp"):
        raise HTTPException(status_code=415, detail="Unsupported file type. Send PNG, JPG, or WEBP.")

    tmp_path = None
    try:
        suffix = os.path.splitext(file.filename)[-1] if file.filename else ".png"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            contents = await file.read()
            tmp.write(contents)
            tmp_path = tmp.name

        result = await analyze_image(tmp_path, relationship_type=relationship_type, other_gender=other_gender, context_note=context_note, requested_mode=requested_mode)
        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


@router.post("/analyze/text")
async def analyze_from_text(
    conversation: str = Form(...),
    relationship_type: str = Form(default="stranger"),
    other_gender: str = Form(default="unknown"),
    context_note: str = Form(default=""),
    requested_mode: str = Form(default="risk"),
):
    if not conversation or not conversation.strip():
        raise HTTPException(status_code=400, detail="Conversation text cannot be empty.")

    try:
        result = await analyze_text(conversation.strip(), relationship_type=relationship_type, other_gender=other_gender, context_note=context_note, requested_mode=requested_mode)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
