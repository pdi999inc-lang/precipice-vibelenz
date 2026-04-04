"""
app/api.py

Glue layer for VibeLenz FastAPI endpoint.

Pipeline:
  Image path  → OCR → turn parser → behavior.py + relationship_dynamics.py → verifier stub → AnalysisResponse
  Text string →       turn parser → behavior.py + relationship_dynamics.py → verifier stub → AnalysisResponse

FLAML verifier is a stub (returns neutral score) until training data is
collected from live endpoint. Replace _run_verifier() when models are ready.
"""

import asyncio
from typing import List, Dict, Any, Optional

from app.ocr import extract_text_from_image          # ocr.py
from app.schemas import AnalysisResponse, Turn        # schemas.py
from app.behavior import analyze_behavior             # behavior.py
from app.relationship_dynamics import analyze_dynamics
from app.analyzer import analyze_text as _analyze_text
from app.interpreter import interpret_analysis as _interpret  # relationship_dynamics.py


# ---------------------------------------------------------------------------
# Turn parser
# ---------------------------------------------------------------------------

def parse_turns(raw_text: str) -> List[Turn]:
    """
    Parse raw conversation text into a list of Turn objects.

    Expected format (flexible):
        Speaker: message text
        Speaker: message text

    Falls back to treating each non-empty line as an unknown-speaker turn
    if no 'Speaker:' pattern is detected.
    """
    turns = []
    lines = [l.strip() for l in raw_text.splitlines() if l.strip()]

    for line in lines:
        if ": " in line:
            speaker, _, message = line.partition(": ")
            turns.append(Turn(speaker=speaker.strip(), message=message.strip()))
        else:
            turns.append(Turn(speaker="unknown", message=line))

    return turns


# ---------------------------------------------------------------------------
# FLAML verifier stub
# ---------------------------------------------------------------------------

def _run_verifier(turns: List[Turn], behavior_result: Dict, dynamics_result: Dict) -> float:
    """
    Stub verifier. Returns neutral confidence score (0.5) until FLAML models
    are trained on live endpoint data.

    Replace with:
        from governance.verifier import score
        return score(turns, behavior_result, dynamics_result)
    """
    return 0.5


# ---------------------------------------------------------------------------
# Core analysis runner (shared by both routes)
# ---------------------------------------------------------------------------

async def _run_pipeline(turns: List[Turn]) -> AnalysisResponse:
    """
    Runs behavior.py and relationship_dynamics.py in parallel,
    then passes results through the verifier stub.
    Returns a fully populated AnalysisResponse.
    """
    if not turns:
        return AnalysisResponse(
            status="error",
            error="No conversation turns found.",
            turns=[],
            behavior=None,
            dynamics=None,
            verifier_score=None,
        )

    # Run behavior and dynamics analysis in parallel
    behavior_result, dynamics_result = await asyncio.gather(
        asyncio.to_thread(analyze_behavior, turns),
        asyncio.to_thread(analyze_dynamics, [{"turn_id": f"T{i+1}", "sender": "other" if i % 2 else "user", "text": t.message} for i, t in enumerate(turns)]),
    )

    verifier_score = _run_verifier(turns, behavior_result, dynamics_result)

    # Run analyzer + interpreter to produce risk_score, lane, diagnosis etc
    raw_text = " ".join(f"{t.speaker}: {t.message}" for t in turns)
    try:
        analysis = _analyze_text(raw_text, use_llm=False)
        narrative = _interpret(analysis, requested_mode="risk")
    except Exception as e:
        logger.warning("analyzer/interpreter failed: %s", e)
        analysis = {}
        narrative = {}

    response = AnalysisResponse(
        status="ok",
        error=None,
        turns=turns,
        behavior=behavior_result,
        dynamics=dynamics_result,
        verifier_score=verifier_score,
    )
    # Attach enriched keys directly to the response dict for downstream consumers
    result = response.model_dump()
    result.update(analysis)
    result.update(narrative)
    return result


# ---------------------------------------------------------------------------
# Public interface (called by routes.py)
# ---------------------------------------------------------------------------

async def analyze_image(image_path: str) -> AnalysisResponse:
    """
    Full pipeline starting from an image file path.
    OCR → turn parser → behavior + dynamics → verifier → AnalysisResponse.
    """
    raw_text = await asyncio.to_thread(extract_text_from_image, image_path)

    if not raw_text or not raw_text.strip():
        return AnalysisResponse(
            status="error",
            error="OCR returned no text. Check image quality or format.",
            turns=[],
            behavior=None,
            dynamics=None,
            verifier_score=None,
        )

    turns = parse_turns(raw_text)
    return await _run_pipeline(turns)


async def analyze_text(raw_text: str) -> AnalysisResponse:
    """
    Full pipeline starting from raw conversation text.
    Turn parser → behavior + dynamics → verifier → AnalysisResponse.
    """
    turns = parse_turns(raw_text)
    return await _run_pipeline(turns)

MAX_TURNS = 200




