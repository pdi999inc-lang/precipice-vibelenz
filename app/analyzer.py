import json
import logging
import os
from typing import Any, Dict
import anthropic

logger = logging.getLogger("vibelenz.analyzer")

SYSTEM_PROMPT = """You are VibeLenz, a safety analysis engine. Analyze conversation text from screenshots for risk signals including: financial requests or pressure (any ask for money, bills, loans, gift cards, crypto, any dollar amount), urgency or deadline pressure, platform shift attempts, emotional manipulation, identity evasion, isolation tactics, investment scams, sob stories designed to elicit money (losing housing, no food, phone bill due), credential harvesting, grooming patterns. Score 0-39 Low, 40-69 Medium, 70-100 High. Critical signals alone score 40+. Err toward flagging. OCR text may have noise. Respond with ONLY valid JSON, no markdown: {"risk_score": <0-100>, "flags": ["signal labels"], "confidence": <0.0-1.0>, "summary": "<summary>", "recommended_action": "<action>", "degraded": false}"""

def analyze_text(text: str) -> Dict[str, Any]:
    try:
        return _run_analysis(text)
    except Exception as e:
        logger.error(f"Analysis failure: {e}")
        return {"risk_score": 100, "flags": ["ANALYSIS_ENGINE_FAILURE"], "confidence": 0.0, "summary": "Analysis engine error. Blocked.", "recommended_action": "Do not proceed.", "degraded": True}

def _run_analysis(text: str) -> Dict[str, Any]:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")
    client = anthropic.Anthropic(api_key=api_key)
    if len(text) > 8000:
        text = text[:8000] + "\n[truncated]"
    message = client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=1024, system=SYSTEM_PROMPT, messages=[{"role": "user", "content": f"Analyze this conversation:\n\n{text}"}])
    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    result = json.loads(raw)
    risk_score = max(0, min(100, int(result.get("risk_score", 0))))
    flags = result.get("flags", ["No signals detected"])
    if not isinstance(flags, list) or len(flags) == 0:
        flags = ["No signals detected"]
    confidence = max(0.0, min(1.0, float(result.get("confidence", 0.5))))
    return {"risk_score": risk_score, "flags": flags, "confidence": confidence, "summary": result.get("summary", "Analysis complete."), "recommended_action": result.get("recommended_action", "No action required."), "degraded": False}
