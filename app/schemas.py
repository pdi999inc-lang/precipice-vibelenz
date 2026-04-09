from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class Turn(BaseModel):
    speaker: str
    message: str

    def to_sender_dict(self) -> Dict[str, Any]:
        return {"sender": self.speaker, "text": self.message}


class RelationshipInsight(BaseModel):
    momentum_direction: str
    energy_balance: str
    intimacy_progression: str
    relationship_stage: str
    momentum_score: float
    compatibility_score: float
    sustainability_score: float
    story_arc: str
    next_natural_step: str
    growth_indicators: List[str] = Field(default_factory=list)
    potential_blockers: List[str] = Field(default_factory=list)
    connection_highlights: List[str] = Field(default_factory=list)
    tension_points: List[str] = Field(default_factory=list)


class BehaviorResult(BaseModel):
    risk_score: float
    flags: List[str] = Field(default_factory=list)
    confidence: float
    degraded: bool = False
    pressure_score: float
    isolation_score: float
    urgency_score: float
    asymmetry_score: float
    deterministic_flag: bool = False


class AnalysisResponse(BaseModel):
    status: str = "ok"
    error: Optional[str] = None
    risk_score: int = 0
    flags: List[str] = Field(default_factory=list)
    confidence: float = 0.5
    degraded: bool = False
    lane: str = "BENIGN"
    primary_label: str = "routine_message"
    human_label: str = ""
    domain_mode: str = "general_unknown"
    presentation_mode: str = "risk"
    requested_mode: str = "risk"
    mode_title: str = ""
    mode_tagline: str = ""
    mode_override_note: str = ""
    diagnosis: str = ""
    reasoning: str = ""
    practical_next_steps: str = ""
    accountability: str = ""
    social_tone: str = ""
    interest_summary: str = ""
    interest_score: Optional[int] = None
    interest_label: str = ""
    llm_enriched: bool = False
    llm_error: Optional[str] = None
    summary: str = ""
    recommended_action: str = ""
    extracted_text: str = ""
    key_signals: List[str] = Field(default_factory=list)
    key_dampeners: List[str] = Field(default_factory=list)
    positive_signals: List[str] = Field(default_factory=list)
    concern_signals: List[str] = Field(default_factory=list)
    alternative_explanations: List[str] = Field(default_factory=list)
    turns: List[Turn] = Field(default_factory=list)
    turn_analysis: Dict[str, Any] = Field(default_factory=dict)
    behavior: Optional[BehaviorResult] = None
    relationship: Optional[RelationshipInsight] = None
    verifier_score: Optional[float] = None

    class Config:
        extra = "allow"


class ErrorResponse(BaseModel):
    error: str
    detail: str
