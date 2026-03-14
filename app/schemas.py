"""
schemas.py - VibeLenz canonical JSON schema definitions.

These are the contract. Do not change field names without backward-compat analysis.
"""

from typing import List
from pydantic import BaseModel, Field


class AnalysisResponse(BaseModel):
    request_id: str = Field(..., description="Unique request identifier for audit trail")
    timestamp: str = Field(..., description="ISO 8601 UTC timestamp of analysis")
    risk_score: int = Field(..., ge=0, le=100, description="Composite risk score 0–100")
    flags: List[str] = Field(..., description="Human-readable signal labels detected")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Analyzer confidence 0.0–1.0")
    summary: str = Field(..., description="Plain-language risk summary")
    recommended_action: str = Field(..., description="Recommended action for the user")
    extracted_text: str = Field(..., description="Raw OCR output from uploaded images")
    degraded: bool = Field(default=False, description="True if system is operating in degraded mode")


class ErrorResponse(BaseModel):
    error: str
    detail: str
