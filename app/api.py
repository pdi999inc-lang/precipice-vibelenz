"""
api.py — VibeLenz core analysis wiring.

Pipeline order:
  Image path  → ocr.py → turn parser → behavior.py + relationship_dynamics.py
              → analyzer_combined → interpreter → AnalysisResponse
  Raw text    →          turn parser → behavior.py + relationship_dynamics.py
              → analyzer_combined → interpreter → AnalysisResponse

FLAML verifier is a stub (returns neutral score) until training data is
collected from live endpoint.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ocr import extract_text_from_image          # returns raw string
from schemas import AnalysisResponse, Turn        # pydantic models
from behavior import analyze_behavior             # safety/deterministic layer
from relationship_dynamics import analyze_dynamics  # connection layer
from analyzer_combined import run_combined        # det + LLM unified analyzer
from interpreter import interpret_analysis        # final interpretation pass

logger = logging.getLogger("vibelenz.api")

# Max turns accepted per request. Conversations beyond this are truncated.
MAX_TURNS = 200


# ---------------------------------------------------------------------------
# Parse metadata
# ---------------------------------------------------------------------------

@dataclass
class ParseResult:
    """
    Wraps the output of parse_turns with diagnostic metadata.

    Attributes:
        turns:         Parsed Turn objects (up to MAX_TURNS).
        unknown_count: Lines that fell back to speaker="unknown".
        truncated:     True if the input exceeded MAX_TURNS and was clipped.
        total_lines:   Total non-empty lines seen before truncation.
    """
    turns: List[Turn] = field(default_factory=list)
    unknown_count: int = 0
    truncated: bool = False
    total_lines: int = 0


# ---------------------------------------------------------------------------
# Turn parser — splits raw text into Turn objects
# ---------------------------------------------------------------------------

def parse_turns(raw_text: str) -> ParseResult:
    """
    Parse raw conversation text into a ParseResult.

    Expected format (flexible):
        Speaker: message text
        Speaker: message text

    Falls back to treating each non-empty line as an unknown-speaker turn
    if no 'Speaker:' pattern is detected.

    Fixes vs original:
    - Splits on ":" not ": " — OCR-dropped spaces no longer lose the speaker.
    - Returns ParseResult with metadata (unknown_count, truncated, total_lines).
    - Truncates to MAX_TURNS and logs a warning if clipped.
    - Guards against malformed colon lines (empty speaker or empty message).
    """
    turns: List[Turn] = []
    unknown_count = 0

    lines = [l.strip() for l in raw_text.strip().splitlines() if l.strip()]
    total_lines = len(lines)

    for line in lines:
        if ":" in line:
            speaker, _, message = line.partition(":")
            speaker = speaker.strip()
            message = message.strip()
            if speaker and message:
                turns.append(Turn(speaker=speaker, message=message))
            else:
                # Malformed colon line (e.g. ":text" or "Speaker:") — treat as unknown
                turns.append(Turn(speaker="unknown", message=line))
                unknown_count += 1
        else:
            turns.append(Turn(speaker="unknown", message=line))
            unknown_count += 1

    truncated = len(turns) > MAX_TURNS
    if truncated:
        logger.warning(
            "parse_turns: %d turns input, truncating to MAX_TURNS=%d",
            len(turns), MAX_TURNS,
        )
        turns = turns[:MAX_TURNS]

    logger.debug(
        "parse_turns: %d turns parsed (%d unknown, truncated=%s)",
        len(turns), unknown_count, truncated,
    )
    return ParseResult(
        turns=turns,
        unknown_count=unknown_count,
        truncated=truncated,
        total_lines=total_lines,
    )


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------

async def _run_pipeline(parse_result: ParseResult) -> AnalysisResponse:
    """
    Runs behavior + relationship_dynamics in parallel, then passes results
    through analyzer_combined and interpreter to produce AnalysisResponse.

    Fixes vs original:
    - Accepts ParseResult (not bare list) for richer logging.
    - asyncio.gather uses return_exceptions=True so one analyzer failure
      doesn't cancel the other — exceptions are caught and returned as
      structured error responses.
    """
    turns = parse_result.turns

    if not turns:
        logger.warning("_run_pipeline: no turns to analyze")
        return AnalysisResponse(
            status="error",
            error="No turns parsed from input.",
            turns=[],
            behavior=None,
            dynamics=None,
            verifier_score=None,
        )

    # Parallel: behavior (safety) + relationship_dynamics (connection).
    # return_exceptions=True: each result is a dict or an Exception.
    results = await asyncio.gather(
        asyncio.to_thread(analyze_behavior, turns),
        asyncio.to_thread(analyze_dynamics, turns),
        return_exceptions=True,
    )
    behavior_result, dynamics_result = results

    if isinstance(behavior_result, Exception):
        logger.error("analyze_behavior raised: %s", behavior_result, exc_info=behavior_result)
        return AnalysisResponse(
            status="error",
            error=f"Behavior analysis failed: {behavior_result}",
            turns=turns,
            behavior=None,
            dynamics=None,
            verifier_score=None,
        )

    if isinstance(dynamics_result, Exception):
        logger.error("analyze_dynamics raised: %s", dynamics_result, exc_info=dynamics_result)
        return AnalysisResponse(
            status="error",
            error=f"Dynamics analysis failed: {dynamics_result}",
            turns=turns,
            behavior=behavior_result,
            dynamics=None,
            verifier_score=None,
        )

    # Combined analyzer (deterministic + optional LLM, use_llm=False until endpoint stable)
    try:
        combined_result = await asyncio.to_thread(
            run_combined, turns, behavior_result, dynamics_result, use_llm=False
        )
    except Exception as e:
        logger.error("run_combined raised: %s", e, exc_info=True)
        return AnalysisResponse(
            status="error",
            error=f"Combined analysis failed: {e}",
            turns=turns,
            behavior=behavior_result,
            dynamics=dynamics_result,
            verifier_score=None,
        )

    # Interpreter — produces final AnalysisResponse
    try:
        response: AnalysisResponse = interpret_analysis(combined_result)
    except Exception as e:
        logger.error("interpret_analysis raised: %s", e, exc_info=True)
        return AnalysisResponse(
            status="error",
            error=f"Interpretation failed: {e}",
            turns=turns,
            behavior=behavior_result,
            dynamics=dynamics_result,
            verifier_score=None,
        )

    logger.info(
        "_run_pipeline: ok — %d turns, unknown=%d, truncated=%s",
        len(turns),
        parse_result.unknown_count,
        parse_result.truncated,
    )

    return response


# ---------------------------------------------------------------------------
# Public entry points called by routes.py
# ---------------------------------------------------------------------------

async def analyze_image(image_path: str) -> AnalysisResponse:
    """
    Full pipeline starting from an image file path.
    OCR → turn parser → behavior + dynamics → combined → interpreter → AnalysisResponse.

    Fixes vs original:
    - OCR call is awaited via asyncio.to_thread (was blocking the event loop).
    - Wrapped in try/except — bad path, corrupt file, or missing Tesseract
      returns a clean AnalysisResponse(status="error") instead of crashing.
    - Empty OCR result returns structured error (was unguarded).
    """
    try:
        raw_text = await asyncio.to_thread(extract_text_from_image, image_path)
    except Exception as e:
        logger.error("OCR failed for %s: %s", image_path, e, exc_info=True)
        return AnalysisResponse(
            status="error",
            error=f"OCR failed: {e}",
            turns=[],
            behavior=None,
            dynamics=None,
            verifier_score=None,
        )

    if not raw_text or not raw_text.strip():
        logger.warning("OCR returned empty text for %s", image_path)
        return AnalysisResponse(
            status="error",
            error="OCR returned no text. Check image quality or format.",
            turns=[],
            behavior=None,
            dynamics=None,
            verifier_score=None,
        )

    parse_result = parse_turns(raw_text)
    return await _run_pipeline(parse_result)


async def analyze_text(raw_text: str) -> AnalysisResponse:
    """
    Full pipeline starting from raw conversation text.
    Turn parser → behavior + dynamics → combined → interpreter → AnalysisResponse.

    Fixes vs original:
    - Guards against empty/whitespace-only input before parsing.
    """
    if not raw_text or not raw_text.strip():
        logger.warning("analyze_text called with empty input")
        return AnalysisResponse(
            status="error",
            error="Input text is empty.",
            turns=[],
            behavior=None,
            dynamics=None,
            verifier_score=None,
        )

    parse_result = parse_turns(raw_text)
    return await _run_pipeline(parse_result)
