"""
analyzer_combined.py — VibeLenz Combined Analysis Wiring

Public interface used by api.py:
    run_combined(turns, behavior_result, dynamics_result, use_llm=False) -> Dict

This module is the bridge between:
    - behavior.py / relationship_dynamics.py (structural signal layers)
    - analyzer.py (full 30-signal VIE deterministic + LLM engine)

run_combined merges behavior and dynamics results into the analyzer output dict,
enriching it with the structured scores from both upstream layers.
The result is passed to interpreter.interpret_analysis() for final copy generation.

All public functions from analyzer.py are re-exported here so that
vie_benchmark.py can import them from analyzer_combined directly.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

# Re-export everything from analyzer.py — benchmark imports from here
from analyzer import (  # noqa: F401
    analyze_text,
    analyze_turns,
    _run_deterministic,
    _apply_relationship_guardrails,
    _sanitize_prohibited_claims,
    _run_llm_analysis,
    _score_evidence,
    _build_research_patch,
    SYSTEM_PROMPT,
    RELATIONSHIP_PROMPT,
    SIGNAL_REGISTRY,
)

logger = logging.getLogger("vibelenz.analyzer_combined")

# Risk floor applied when behavior.py deterministic_flag fires.
# Matches the WARN threshold in analyzer.py vie_action logic.
DETERMINISTIC_FLAG_FLOOR = 50


def run_combined(
    turns: List[Any],
    behavior_result: Dict[str, Any],
    dynamics_result: Dict[str, Any],
    use_llm: bool = False,
) -> Dict[str, Any]:
    """
    Merge behavior.py and relationship_dynamics.py outputs with the
    analyzer engine result.

    Parameters
    ----------
    turns            : List of Turn objects (from api.py parse_turns).
    behavior_result  : Dict from behavior.analyze_behavior() — BehaviorResult fields.
    dynamics_result  : Dict from relationship_dynamics.analyze_dynamics() — DynamicsResult fields.
    use_llm          : If True, use Claude API (expensive). Default False until
                       live endpoint is stable.

    Returns
    -------
    Enriched analysis dict ready for interpreter.interpret_analysis().
    """
    # Convert Turn objects to plain text for the analyzer engine
    if turns and hasattr(turns[0], "message"):
        # Pydantic Turn objects from schemas.py
        raw_text = "\n".join(
            f"{t.speaker}: {t.message}" for t in turns
        )
        # Also build the turn-dict format for analyze_turns()
        turn_dicts = [
            {"turn_id": f"T{i+1}", "sender": "other" if i % 2 else "user", "text": t.message}
            for i, t in enumerate(turns)
        ]
    else:
        # Already plain dicts (test path)
        raw_text = "\n".join(
            f"{t.get('sender', 'unknown')}: {t.get('text', '')}" for t in turns
        )
        turn_dicts = turns

    # Run the core analyzer engine
    try:
        result = analyze_text(raw_text, use_llm=use_llm)
    except Exception as e:
        logger.error("run_combined: analyze_text failed — %s", e, exc_info=True)
        result = _run_deterministic(raw_text, "stranger")

    # Merge behavior signals into result
    if behavior_result:
        result["behavior"] = behavior_result
        # Promote deterministic_flag if behavior layer fired it
        if behavior_result.get("deterministic_flag"):
            result["deterministic_flag"] = True
            if result.get("risk_score", 0) < DETERMINISTIC_FLAG_FLOOR:
                result["risk_score"] = max(result.get("risk_score", 0), DETERMINISTIC_FLAG_FLOOR)
                result["risk_level"] = "MEDIUM"

    # Merge dynamics signals into result
    if dynamics_result:
        result["dynamics"] = dynamics_result
        # Surface insufficient_data flag
        if dynamics_result.get("insufficient_data"):
            result["insufficient_data"] = True

    # Verifier stub — stays 0.5 until FLAML models trained
    result["verifier_score"] = 0.5

    logger.info(
        "run_combined: risk=%d lane=%s degraded=%s",
        result.get("risk_score", 0),
        result.get("lane", "UNKNOWN"),
        result.get("degraded", False),
    )

    return result
