"""
schemas.py — VibeLenz canonical Pydantic models.

Contract: do not change field names without backward-compat analysis.

AnalysisResponse structure:
    - status         : "ok" | "error"
    - error          : None or error message string
    - turns          : list of parsed Turn objects
    - behavior       : output of behavior.py (safety/deterministic layer)
    - dynamics       : output of relationship_dynamics.py (connection layer)
    - verifier_score : float 0.0–1.0 (FLAML stub returns 0.5 until trained)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Turn — atomic unit of conversation
# ---------------------------------------------------------------------------

class Turn(BaseModel):
    speaker: str = Field(..., description="Speaker label extracted from input text.")
    message: str = Field(..., description="Message content for this turn.")


# ---------------------------------------------------------------------------
# BehaviorResult — output contract for behavior.py
# ---------------------------------------------------------------------------

class BehaviorResult(BaseModel):
    """
    Safety/deterministic layer output.
    Produced by behavior.py. Fields mirror BehaviorProfile.to_feature_vector().
    """
    risk_score: float = Field(..., ge=0.0, le=1.0, description="Overall pressure/risk score.")
    flags: List[str] = Field(default_factory=list, description="Active behavioral flags.")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence in risk assessment.")
    degraded: bool = Field(False, description="True if analysis ran in degraded/fail-closed mode.")
    pressure_score: float = Field(0.0, ge=0.0, le=1.0)
    isolation_score: float = Field(0.0, ge=0.0, le=1.0)
    urgency_score: float = Field(0.0, ge=0.0, le=1.0)
    asymmetry_score: float = Field(0.0, ge=0.0, le=1.0)
    deterministic_flag: bool = Field(False, description="Hard gate: fired before FLAML.")


# ---------------------------------------------------------------------------
# DynamicsResult — output contract for relationship_dynamics.py
# ---------------------------------------------------------------------------

class DynamicsResult(BaseModel):
    """
    Connection/relationship layer output.
    Produced by relationship_dynamics.py. Primary consumer-facing signal.
    """
    momentum_direction: str = Field(
        "unclear",
        description="'building' | 'maintaining' | 'fading' | 'unclear'"
    )
    energy_balance: str = Field(
        "unclear",
        description="'balanced' | 'user_leading' | 'other_leading' | 'mismatched'"
    )
    intimacy_progression: str = Field(
        "unclear",
        description="'healthy' | 'rushing' | 'stalled' | 'unclear'"
    )
    relationship_stage: str = Field(
        "initial_contact",
        description="'initial_contact' | 'building_rapport' | 'exploring_compatibility' | 'deepening' | 'moving_too_fast'"
    )
    momentum_score: float = Field(0.0, ge=0.0, le=1.0)
    compatibility_score: float = Field(0.0, ge=0.0, le=1.0)
    sustainability_score: float = Field(0.0, ge=0.0, le=1.0)
    story_arc: str = Field("", description="Plain-language summary of conversation trajectory.")
    next_natural_step: str = Field("", description="What logically happens next.")
    growth_indicators: List[str] = Field(default_factory=list)
    potential_blockers: List[str] = Field(default_factory=list)
    connection_highlights: List[str] = Field(default_factory=list)
    tension_points: List[str] = Field(default_factory=list)
    insufficient_data: bool = Field(False, description="True if fewer than 3 turns — dynamics not computed.")


# ---------------------------------------------------------------------------
# AnalysisResponse — top-level API response contract
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# RelationshipInsight — alias of DynamicsResult
# ---------------------------------------------------------------------------
# relationship_dynamics.py and vie_benchmark.py both import RelationshipInsight.
# DynamicsResult is the canonical schema name; RelationshipInsight is an alias
# preserved for backward compatibility and module-level legibility.
# They are the same model — changing field names on either requires backward-
# compat analysis per the contract note at the top of this file.

RelationshipInsight = DynamicsResult


# ---------------------------------------------------------------------------
# AnalysisResponse — top-level API response contract
# ---------------------------------------------------------------------------

class AnalysisResponse(BaseModel):
    """
    Top-level response returned by all /v1/analyze/* endpoints.
    """
    status: str = Field(..., description="'ok' | 'error'")
    error: Optional[str] = Field(None, description="Error message if status='error', else None.")
    turns: List[Turn] = Field(default_factory=list, description="Parsed conversation turns.")
    behavior: Optional[BehaviorResult] = Field(None, description="Safety layer output.")
    dynamics: Optional[DynamicsResult] = Field(None, description="Relationship layer output.")
    verifier_score: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
        description="FLAML verifier confidence. Stub returns 0.5 until trained."
    )
