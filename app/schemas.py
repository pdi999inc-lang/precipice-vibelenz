from __future__ import annotations
from typing import List, Optional
from pydantic import BaseModel, Field

class Turn(BaseModel):
    speaker: str = Field(..., description="Speaker label.")
    message: str = Field(..., description="Message content.")

class BehaviorResult(BaseModel):
    risk_score: float = Field(..., ge=0.0, le=1.0)
    flags: List[str] = Field(default_factory=list)
    confidence: float = Field(..., ge=0.0, le=1.0)
    degraded: bool = Field(False)
    pressure_score: float = Field(0.0, ge=0.0, le=1.0)
    isolation_score: float = Field(0.0, ge=0.0, le=1.0)
    urgency_score: float = Field(0.0, ge=0.0, le=1.0)
    asymmetry_score: float = Field(0.0, ge=0.0, le=1.0)
    deterministic_flag: bool = Field(False)

class RelationshipInsight(BaseModel):
    momentum_direction: str = Field("unclear")
    energy_balance: str = Field("unclear")
    intimacy_progression: str = Field("unclear")
    relationship_stage: str = Field("initial_contact")
    momentum_score: float = Field(0.0, ge=0.0, le=1.0)
    compatibility_score: float = Field(0.0, ge=0.0, le=1.0)
    sustainability_score: float = Field(0.0, ge=0.0, le=1.0)
    story_arc: str = Field("")
    next_natural_step: str = Field("")
    growth_indicators: List[str] = Field(default_factory=list)
    potential_blockers: List[str] = Field(default_factory=list)
    connection_highlights: List[str] = Field(default_factory=list)
    tension_points: List[str] = Field(default_factory=list)
    insufficient_data: bool = Field(False)

class DynamicsResult(BaseModel):
    momentum_direction: str = Field("unclear")
    energy_balance: str = Field("unclear")
    intimacy_progression: str = Field("unclear")
    relationship_stage: str = Field("initial_contact")
    momentum_score: float = Field(0.0, ge=0.0, le=1.0)
    compatibility_score: float = Field(0.0, ge=0.0, le=1.0)
    sustainability_score: float = Field(0.0, ge=0.0, le=1.0)
    story_arc: str = Field("")
    next_natural_step: str = Field("")
    growth_indicators: List[str] = Field(default_factory=list)
    potential_blockers: List[str] = Field(default_factory=list)
    connection_highlights: List[str] = Field(default_factory=list)
    tension_points: List[str] = Field(default_factory=list)
    insufficient_data: bool = Field(False)

class AnalysisResponse(BaseModel):
    status: str = Field(...)
    error: Optional[str] = Field(None)
    turns: List[Turn] = Field(default_factory=list)
    behavior: Optional[BehaviorResult] = Field(None)
    dynamics: Optional[DynamicsResult] = Field(None)
    verifier_score: Optional[float] = Field(None, ge=0.0, le=1.0)
