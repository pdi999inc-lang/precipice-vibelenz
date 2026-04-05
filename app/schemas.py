"""
schemas.py - VibeLenz canonical JSON schema definitions.

These are the contract. Do not change field names without backward-compat analysis.

AnalysisResponse structure:
    - Safety layer: risk_score, flags, confidence, degraded
    - Relationship layer: relationship (RelationshipInsight)
    - Shared: request_id, timestamp, summary, recommended_action, extracted_text
"""

from typing import List, Optional
from pydantic import BaseModel, Field


class RelationshipInsight(BaseModel):
    """
    Consumer-facing relationship intelligence output.
    Primary output layer for dating conversation analysis.
    Produced by relationship_dynamics.py.
    """
    # Core dynamics
    momentum_direction: str = Field(
        ...,
        description="Conversation trajectory: 'building' | 'maintaining' | 'fading' | 'unclear'"
    )
    energy_balance: str = Field(
        ...,
        description="Effort distribution: 'balanced' | 'user_leading' | 'other_leading' | 'mismatched'"
    )
    intimacy_progression: str = Field(
        ...,
        description="Emotional pace: 'healthy' | 'rushing' | 'stalled' | 'unclear'"
    )
    relationship_stage: str = Field(
        ...,
        description="Development stage: 'initial_contact' | 'building_rapport' | 'exploring_compatibility' | 'deepening' | 'moving_too_fast'"
    )

    # Scores (0.0–1.0)
    momentum_score: float = Field(..., ge=0.0, le=1.0, description="Forward energy score")
    compatibility_score: float = Field(..., ge=0.0, le=1.0, description="How well they're clicking")
    sustainability_score: float = Field(..., ge=0.0, le=1.0, description="Long-term potential score")

    # Narrative output (consumer-facing language)
    story_arc: str = Field(..., description="Plain-language summary of what's happening between these people")
    next_natural_step: str = Field(..., description="What would logically happen next")

    # Lists
    growth_indicators: List[str] = Field(default_factory=list, description="Signals this could develop positively")
    potential_blockers: List[str] = Field(default_factory=list, description="What could derail this connection")
    connection_highlights: List[str] = Field(default_factory=list, description="Moments of genuine connection")
    tension_points: List[str] = Field(default_factory=list, description="Areas of friction or uncertainty")


class AnalysisResponse(BaseModel):
    """
    Top-level API response contract.
    Do not change field names without backward-compat analysis.
    """
    # Audit / identity
    request_id: str = Field(..., description="Unique request identifier for audit trail")
    timestamp: str = Field(..., description="ISO 8601 UTC timestamp of analysis")

    # Safety layer (behavior.py → FLAML verifier)
    risk_score: int = Field(..., ge=0, le=100, description="Composite risk score 0–100")
    flags: List[str] = Field(..., description="Human-readable signal labels detected")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Analyzer confidence 0.0–1.0")
    degraded: bool = Field(default=False, description="True if system is operating in degraded mode")

    # Shared output
    summary: str = Field(..., description="Plain-language risk summary")
    recommended_action: str = Field(..., description="Recommended action for the user")
    extracted_text: str = Field(..., description="Raw OCR output from uploaded images")

    # Relationship layer (relationship_dynamics.py) — Optional: absent if insufficient turns
    relationship: Optional[RelationshipInsight] = Field(
        default=None,
        description="Relationship intelligence output. None if fewer than 3 turns detected."
    )


class ErrorResponse(BaseModel):
    error: str
    detail: str