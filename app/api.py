from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.ocr import extract_text_from_images
from app.schemas import AnalysisResponse, Turn
from app.behavior import analyze_behavior
from app.relationship_dynamics import analyze_dynamics
from app.analyzer_combined import run_combined
from app.interpreter import interpret_analysis

logger = logging.getLogger("vibelenz.api")

MAX_TURNS = 200


@dataclass
class ParseResult:
    turns: List[Turn] = field(default_factory=list)
    unknown_count: int = 0
    truncated: bool = False
    total_lines: int = 0


def parse_turns(raw_text: str) -> ParseResult:
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
                turns.append(Turn(speaker="unknown", message=line))
                unknown_count += 1
        else:
            turns.append(Turn(speaker="unknown", message=line))
            unknown_count += 1
    truncated = len(turns) > MAX_TURNS
    if truncated:
        logger.warning("parse_turns: %d turns, truncating to %d", len(turns), MAX_TURNS)
        turns = turns[:MAX_TURNS]
    logger.debug("parse_turns: %d turns (%d unknown, truncated=%s)", len(turns), unknown_count, truncated)
    return ParseResult(turns=turns, unknown_count=unknown_count, truncated=truncated, total_lines=total_lines)


def _turns_to_dicts(turns: List[Turn]) -> List[Dict[str, Any]]:
    speaker_map: Dict[str, str] = {}
    sender_order = ["user", "other"]
    result = []
    for turn in turns:
        sp = turn.speaker
        if sp not in speaker_map:
            idx = len(speaker_map)
            speaker_map[sp] = sender_order[idx] if idx < len(sender_order) else "other"
        result.append({"sender": speaker_map[sp], "text": turn.message})
    return result


async def _run_pipeline(
    parse_result: ParseResult,
    raw_text: str = "",
    relationship_type: str = "stranger",
    other_gender: str = "unknown",
    context_note: str = "",
    requested_mode: str = "risk",
) -> AnalysisResponse:
    turns = parse_result.turns
    if not turns:
        logger.warning("_run_pipeline: no turns to analyze")
        return AnalysisResponse(status="error", error="No turns parsed from input.", turns=[], behavior=None, verifier_score=None)

    turn_dicts = _turns_to_dicts(turns)

    results = await asyncio.gather(
        asyncio.to_thread(analyze_behavior, turn_dicts),
        asyncio.to_thread(analyze_dynamics, turn_dicts),
        return_exceptions=True,
    )
    behavior_result, dynamics_result = results

    if isinstance(behavior_result, Exception):
        logger.error("analyze_behavior raised: %s", behavior_result)
        return AnalysisResponse(status="error", error=f"Behavior analysis failed: {behavior_result}", turns=turns, behavior=None, verifier_score=None)

    if isinstance(dynamics_result, Exception):
        logger.error("analyze_dynamics raised: %s", dynamics_result)
        return AnalysisResponse(status="error", error=f"Dynamics analysis failed: {dynamics_result}", turns=turns, behavior=behavior_result, verifier_score=None)

    try:
        combined_result = await asyncio.to_thread(run_combined, turns, behavior_result, dynamics_result, use_llm=False)
    except Exception as e:
        logger.error("run_combined raised: %s", e)
        return AnalysisResponse(status="error", error=f"Combined analysis failed: {e}", turns=turns, behavior=behavior_result, verifier_score=None)

    try:
        response = interpret_analysis(
            combined_result,
            extracted_text=raw_text,
            relationship_type=relationship_type,
            other_gender=other_gender,
            context_note=context_note,
            requested_mode=requested_mode,
            use_llm=False,
        )
    except Exception as e:
        logger.error("interpret_analysis raised: %s", e)
        return AnalysisResponse(status="error", error=f"Interpretation failed: {e}", turns=turns, behavior=behavior_result, verifier_score=None)

    logger.info("_run_pipeline: ok — %d turns, unknown=%d, truncated=%s", len(turns), parse_result.unknown_count, parse_result.truncated)
    return response


async def analyze_image(
    image_path: str,
    relationship_type: str = "stranger",
    other_gender: str = "unknown",
    context_note: str = "",
    requested_mode: str = "risk",
) -> AnalysisResponse:
    try:
        with open(image_path, "rb") as f:
            image_bytes = f.read()
        raw_text = await asyncio.to_thread(extract_text_from_images, [image_bytes])
    except Exception as e:
        logger.error("OCR failed for %s: %s", image_path, e)
        return AnalysisResponse(status="error", error=f"OCR failed: {e}", turns=[], behavior=None, verifier_score=None)

    if not raw_text or not raw_text.strip():
        return AnalysisResponse(status="error", error="OCR returned no text.", turns=[], behavior=None, verifier_score=None)

    parse_result = parse_turns(raw_text)
    return await _run_pipeline(parse_result, raw_text=raw_text, relationship_type=relationship_type, other_gender=other_gender, context_note=context_note, requested_mode=requested_mode)


async def analyze_text(
    raw_text: str,
    relationship_type: str = "stranger",
    other_gender: str = "unknown",
    context_note: str = "",
    requested_mode: str = "risk",
) -> AnalysisResponse:
    if not raw_text or not raw_text.strip():
        return AnalysisResponse(status="error", error="Input text is empty.", turns=[], behavior=None, verifier_score=None)

    parse_result = parse_turns(raw_text)
    return await _run_pipeline(parse_result, raw_text=raw_text, relationship_type=relationship_type, other_gender=other_gender, context_note=context_note, requested_mode=requested_mode)
