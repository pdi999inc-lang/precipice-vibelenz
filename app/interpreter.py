from __future__ import annotations

import os
import json
import httpx
from typing import Any, Dict, List


def _clean(items: List[str]) -> List[str]:
    return [str(x).strip() for x in (items or []) if str(x).strip()]


def _has(items: List[str], value: str) -> bool:
    return value in _clean(items)


def _human_label(primary_label: str, lane: str, domain_mode: str) -> str:
    mapping = {
        "playful_reengagement": "playful reconnection",
        "light_sexual_reciprocity": "light sexual reciprocity",
        "warm_receptivity": "warm receptivity",
        "confusion_then_repair": "confusion that clears",
        "casual_flirtation": "casual flirtation",
        "low_information_neutral": "low-stakes interaction",
        "routine_host_message": "routine logistics",
        "transactional_extraction_pattern": "transactional risk pattern",
        "pressure_with_boundary_violation": "pressure pattern",
    }
    return mapping.get(primary_label, primary_label.replace("_", " "))


def _social_tone(result: Dict[str, Any]) -> str:
    positives = _clean(result.get("positive_signals", []))
    primary_label = str(result.get("primary_label", "low_information_neutral"))

    if "sexual_reciprocity_present" in positives or primary_label == "light_sexual_reciprocity":
        return "playful, flirtatious, and reciprocal"
    if primary_label == "playful_reengagement":
        return "warm after initial confusion"
    if "warm_receptivity_present" in positives or primary_label == "warm_receptivity":
        return "open, warm, and responsive"
    if primary_label == "confusion_then_repair":
        return "awkward at first, then repaired"
    if primary_label == "casual_flirtation":
        return "light and socially positive"
    return "fairly low-stakes"


def _interest_summary(result: Dict[str, Any]) -> str:
    label = str(result.get("interest_label", "Not Applicable"))
    primary_label = str(result.get("primary_label", "low_information_neutral"))

    if label and label != "Not Applicable":
        if label.lower() == "high":
            return "good receptivity"
        if label.lower() == "moderate":
            return "some real receptivity"
        return label

    mapping = {
        "playful_reengagement": "good receptivity",
        "light_sexual_reciprocity": "clear playful interest",
        "warm_receptivity": "positive openness",
        "confusion_then_repair": "improving energy",
        "casual_flirtation": "light interest",
    }
    return mapping.get(primary_label, "context dependent")


def _risk_override(result: Dict[str, Any]) -> bool:
    lane = str(result.get("lane", "BENIGN"))
    risk_level = str(result.get("risk_level", "LOW")).upper()
    return lane in {"FRAUD", "COERCION_RISK"} or risk_level == "HIGH"


def _risk_copy(out: Dict[str, Any]) -> Dict[str, Any]:
    lane = str(out.get("lane", "BENIGN"))
    domain_mode = str(out.get("domain_mode", "general_unknown"))

    if lane == "FRAUD":
        if domain_mode == "housing_rental":
            diagnosis = "This looks more like a setup than a normal rental conversation."
            reasoning = "The concern is the sequence. Once verification gets inverted, money enters the picture, or the story starts shifting, the interaction stops reading like normal logistics and starts reading like a transactional risk pattern."
            next_steps = "Slow it down immediately. Verify ownership, identity, and the platform story independently before you give money, documents, or trust."
            accountability = "Do not talk yourself out of obvious risk just because the tone sounds polite, charming, or routine."
        else:
            diagnosis = "This reads more like a risk pattern than a normal interaction."
            reasoning = "What matters most is not one isolated line, but the overall pattern of pressure, extraction, contradiction, or control."
            next_steps = "Pause the interaction and verify independently before you give money, sensitive information, or control."
            accountability = "Do not explain away real risk signals just because the delivery feels smooth."
    elif lane == "COERCION_RISK":
        diagnosis = "This is starting to feel like pressure, not just awkwardness."
        reasoning = "What pushes this upward is not mere discomfort. The visible pattern starts to lean on pressure or boundary friction, which matters more than tone alone."
        next_steps = "Tighten the boundary. State it clearly once, then watch whether the other person respects it without needing a long argument."
        accountability = "Do not explain away pressure just because it arrives wrapped in charm, confusion, or emotion."
    else:
        diagnosis = "This does not currently read like a strong risk pattern."
        reasoning = "Nothing shown here strongly supports fraud, coercion, or extraction as the main story."
        next_steps = "Stay observant, but do not overreact to a conversation that does not clearly justify a high-risk read."
        accountability = "Do not manufacture danger where the evidence is still thin."

    out["presentation_mode"] = "risk"
    out["mode_title"] = "Risk Analysis"
    out["mode_tagline"] = "Sharper read on contradiction, pressure, extraction, and protective next steps."
    out["human_label"] = _human_label(str(out.get("primary_label", "")), lane, domain_mode)
    out["diagnosis"] = diagnosis
    out["reasoning"] = reasoning
    out["practical_next_steps"] = next_steps
    out["accountability"] = accountability
    out["social_tone"] = "Not the focus here"
    out["interest_summary"] = "Not the focus here"
    out["mode_override_note"] = ""
    return out


def _connection_copy(out: Dict[str, Any]) -> Dict[str, Any]:
    primary_label = str(out.get("primary_label", "low_information_neutral"))
    coaching = _clean(out.get("coaching_markers", []))

    if primary_label == "playful_reengagement":
        diagnosis = "She was confused, then embarrassed, then she came back around — and that arc is actually more telling than if it had gone smoothly."
        reasoning = (
            "She probably did not remember clearly at first, then pieces started coming back, "
            "then the screenshot or context scrambled it again, and once she fully placed you she settled down. "
            "This does not look like a calculated rejection. It looks like someone who was scatterbrained, embarrassed, "
            "and trying to recover. The energy she showed after the confusion cleared — 'I still do,' 'I want your genes,' 'Yay' — "
            "that is not what rejection looks like. A girl who is genuinely turned off does not go there. "
            "She feels more like someone who is messy and in-the-moment, not someone who is shutting you down. "
            "Honest probability read: still open to talking at 60 to 70 percent. "
            "Genuinely into you in a stable way, more like 35 to 45. "
            "But she was not rejecting you — she was confused and trying to recover."
        )
        next_steps = (
            "Do not make this heavier than it is. Do not ask her why she said it was gross, "
            "or whether she actually remembers you, or what she meant. That will make it worse. "
            "Treat it like a slightly awkward reconnection that already repaired itself. "
            "Something like 'You're good. I'll allow the new phone excuse.' or 'We'll call it temporary memory loss.' "
            "That keeps it easy and puts you back in control of the tone."
        )
        accountability = (
            "Your bigger mistake was trying to get personal connection and app validation out of the same exchange. "
            "You had playful sexual energy going and then switched into founder demo mode. That cools things down. "
            "Flirt if you are flirting. Pitch if you are pitching. Mixing them weakens both. "
            "She does not look uninterested — she looks disorganized. You still have room. Stop making it a case file."
        )

    elif primary_label == "light_sexual_reciprocity":
        diagnosis = "There is real flirtatious energy here and she is matching it, not just tolerating it."
        reasoning = (
            "This is not just politeness. She is leaning in. The reciprocal tone is visible — "
            "she is not deflecting, redirecting, or going cold. That is the signal that matters "
            "more than anything else in an early exchange."
        )
        next_steps = (
            "Stay with it. Let the energy breathe. The moment you start explaining yourself "
            "or pivoting into serious mode, you lose the thread."
        )
        accountability = "Do not talk yourself out of chemistry that is already working."

    elif primary_label == "warm_receptivity":
        diagnosis = "She is open and engaged — not guarded, not pulling back."
        reasoning = (
            "What stands out here is not intensity, it is the absence of resistance. "
            "She is not looking for an exit. That openness is quiet but it is real, and it is a better signal "
            "than surface enthusiasm that disappears the moment things get less easy."
        )
        next_steps = (
            "Keep the tone easy and person-focused. Let consistency do the work from here — "
            "not pressure, not grand gestures."
        )
        accountability = "Warm does not mean locked in. Do not skip the part where you actually build something."

    elif primary_label == "confusion_then_repair":
        diagnosis = "It started awkward, but she worked to fix it — and that is the part that actually matters."
        reasoning = (
            "The rough opening is not the story. People who are checked out do not bother repairing the energy. "
            "The new phone excuse is believable enough — not guaranteed true, but believable. "
            "What matters more is what she did once the confusion cleared. She came back. "
            "That means there was something worth recovering in her mind. "
            "She is not locked in and she is not super consistent, but she is not shutting you down either."
        )
        next_steps = (
            "Do not drag the awkward moment back into the conversation. She already moved past it — follow her lead. "
            "Keep it light. Something easy that acknowledges the weirdness without dwelling on it "
            "puts you back in the right lane."
        )
        accountability = (
            "Stop overanalyzing her confusion when the obvious read is simpler: "
            "she was embarrassed, she recovered, and she is still open. "
            "You are not locked into the worst version of how this started."
        )

    elif primary_label == "casual_flirtation":
        diagnosis = "Light, easy, and going in the right direction."
        reasoning = (
            "Nothing here is heavy or loaded. The tone is playful and the energy is positive. "
            "What is absent matters as much as what is present — no defensiveness, no pulling back, "
            "no mixed signals that would justify a complicated read."
        )
        next_steps = "Keep it light. Do not make it heavier than it needs to be right now."
        accountability = "Not every good thing needs to be analyzed into the ground. Sometimes easy is just easy."


    elif primary_label == "high_intent_mutual":
        pressure_present = "pressure_present" in _clean(out.get("concern_signals", []))
        fear_driven = "fear_driven_urgency" in _clean(out.get("concern_signals", []))
        goal_sub = "goal_substitution" in _clean(out.get("concern_signals", []))

        diagnosis = "Both people are showing up with real intent — this conversation has weight to it."
        reasoning = (
            "The alignment here is not surface-level. "
            "There is shared vision, reciprocal investment, and both people are being direct about what they want. "
            "That combination is rarer than it looks and worth taking seriously."
        )
        if fear_driven or goal_sub:
            reasoning += (
                " That said, some of the urgency on her side reads as fear-driven rather than vision-driven. "
                "She may be operating from a scarcity mindset — avoiding an outcome rather than building toward one. "
                "That does not disqualify the connection, but it means the timeline pressure is hers, not necessarily yours. "
                "Enter this clearly, not reactively."
            )
        next_steps = (
            "The conversation has done its job. The next move is a real-world meeting — "
            "not another exchange, not more rapport-building. "
            "Ask directly. Something grounded: 'I think we have covered enough ground — are you free this week?'"
        )
        if pressure_present:
            accountability = (
                "She is moving fast and the energy is compelling. "
                "Make sure you are choosing her clearly rather than getting swept into her timeline. "
                "Fast is fine if it is mutual. Fast because she is scared is a different thing."
            )
        else:
            accountability = (
                "You have the alignment. The only way this stalls now is if you stay in the conversation "
                "instead of converting it. Get off the app."
            )

    elif primary_label == "fear_driven_urgency":
        diagnosis = "She knows what she wants — but some of that urgency is about avoiding something, not just building toward it."
        reasoning = (
            "The timeline pressure, the early ultimatums, the substitution of outcomes — "
            "these are not red flags exactly, but they are signals worth reading clearly. "
            "A person operating from scarcity will move fast, commit fast, and may accept a suboptimal match "
            "to resolve the fear. The connection can still be real. "
            "But the pressure is coming from her internal clock, not from what is actually between you yet."
        )
        next_steps = (
            "Do not match her urgency. Stay grounded. "
            "If the connection is real it will hold at your pace too. "
            "If it only works at her pace, that tells you something important."
        )
        accountability = (
            "The risk here is not her intent — it is your clarity. "
            "Make sure you are making an active choice, not just going along because the energy is strong."
        )

    elif primary_label == "mixed_intent_genuine":
        diagnosis = "The signals are mixed, but not in a way that reads as calculated — this just needs more time."
        reasoning = (
            "There is enough positive signal here to take seriously, "
            "but not enough yet to make a confident read in either direction. "
            "That is not a problem — it is just where this conversation actually is. "
            "Pushing for a conclusion before the data supports it will only produce a wrong one."
        )
        next_steps = (
            "One or two more direct exchanges will tell you more than any analysis of what you have now. "
            "Ask something that requires a real answer — not small talk."
        )
        accountability = (
            "Mixed does not mean bad. It means early. "
            "Stop trying to resolve ambiguity that has not had time to resolve itself."
        )

    else:
        diagnosis = "This is a real human interaction — low stakes, not a threat, just still early."
        concern_signals = _clean(out.get("concern_signals", []))
        has_pressure = any("pressure" in s for s in concern_signals)
        reasoning = (
            "It reads like a normal exchange between two people who are still figuring out the dynamic. "
            "That is not a bad thing — it just means the picture is not complete yet. "
            + ("There are some early friction signals worth keeping an eye on as things develop. " if has_pressure else "Nothing here points to strong pressure or bad intent. ")
            + "A few more exchanges will tell you more than any analysis of what you already have."
        )
        next_steps = (
            "Treat it lightly. Let the next few exchanges do the work "
            "instead of trying to force a conclusion from limited data."
        )
        accountability = (
            "Stop trying to solve it before it has had time to develop. "
            "You do not have enough information yet to make a hard call — and that is okay."
        )

    if "self_pitch_present" in coaching and primary_label in {"playful_reengagement", "light_sexual_reciprocity", "warm_receptivity"}:
        next_steps = (
            "Keep it personal from here. You already have chemistry working — "
            "do not redirect it into a product demo. That trade is almost never worth it."
        )
        accountability = (
            "Flirt if you are flirting. Pitch if you are pitching. "
            "Mixing them weakens both and gives you fake signal on both ends."
        )

    out["presentation_mode"] = "connection"
    out["mode_title"] = "Connection Analysis"
    out["mode_tagline"] = "Warm read on chemistry, receptivity, emotional movement, and what to do next."
    out["human_label"] = _human_label(str(out.get("primary_label", "")), str(out.get("lane", "")), str(out.get("domain_mode", "")))
    out["diagnosis"] = diagnosis
    out["reasoning"] = reasoning
    out["practical_next_steps"] = next_steps
    out["accountability"] = accountability
    out["social_tone"] = _social_tone(out)
    out["interest_summary"] = _interest_summary(out)
    out["mode_override_note"] = ""
    return out


def _llm_enrich(
    result: Dict[str, Any],
    extracted_text: str,
    presentation_mode: str,
    diagnosis: str,
    reasoning: str,
    practical_next_steps: str,
    accountability: str,
) -> Dict[str, Any]:
    """
    Calls Anthropic to enrich deterministic copy with LLM-generated insight.
    Falls back to deterministic output if the call fails.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {
            "diagnosis": diagnosis,
            "reasoning": reasoning,
            "practical_next_steps": practical_next_steps,
            "accountability": accountability,
            "llm_enriched": False,
            "llm_error": "no_api_key",
        }

    primary_label = result.get("primary_label", "unknown")
    concern_signals = result.get("concern_signals", [])
    positive_signals = result.get("positive_signals", [])
    connection_score = result.get("connection_score", None)
    risk_score = result.get("risk_score", None)
    lane = result.get("lane", "BENIGN")

    if presentation_mode == "connection":
        system_prompt = """You are VibeLenz, a relationship dynamics analyst.
Your job is to give the user a warm, honest, specific read on their dating conversation.
Write like a thoughtful friend giving real talk — not a therapist, not a security scanner.
Never use clinical language. Never use words like: pressure, danger, bad intent, threat, coercion, red flag.
Be specific to what actually happened in the conversation. Avoid generic advice.
You will receive: the raw conversation, the detected signals, and a deterministic draft.
Your job is to improve the draft — make it more specific, more human, more useful.
Return ONLY a JSON object with keys: diagnosis, reasoning, practical_next_steps, accountability.
No preamble. No markdown. No explanation. Raw JSON only."""

        user_prompt = f"""CONVERSATION:
{extracted_text}

DETECTED SIGNALS:
Positive: {json.dumps(positive_signals)}
Concern: {json.dumps(concern_signals)}
Primary label: {primary_label}
Lane: {lane}

DETERMINISTIC DRAFT:
Diagnosis: {diagnosis}
Reasoning: {reasoning}
Next steps: {practical_next_steps}
Accountability: {accountability}

Rewrite the draft to be more specific to this actual conversation.
Keep the same structure. Make it warmer, more human, more actionable.
Return raw JSON only."""

    else:
        system_prompt = """You are VibeLenz, a conversation safety analyst.
Your job is to give the user a clear, honest read on risk signals in their conversation.
Be direct and specific. Do not catastrophize. Do not minimize.
You will receive: the raw conversation, the detected signals, and a deterministic draft.
Improve the draft — make it more specific to what actually happened.
Return ONLY a JSON object with keys: diagnosis, reasoning, practical_next_steps, accountability.
No preamble. No markdown. No explanation. Raw JSON only."""

        user_prompt = f"""CONVERSATION:
{extracted_text}

DETECTED SIGNALS:
Risk signals: {json.dumps(result.get("key_signals", []))}
Concern signals: {json.dumps(concern_signals)}
Primary label: {primary_label}
Lane: {lane}
Risk score: {risk_score}

DETERMINISTIC DRAFT:
Diagnosis: {diagnosis}
Reasoning: {reasoning}
Next steps: {practical_next_steps}
Accountability: {accountability}

Rewrite the draft to be more specific to this actual conversation.
Return raw JSON only."""

    try:
        response = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 1024,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
            },
            timeout=15.0,
        )
        response.raise_for_status()
        data = response.json()
        raw = data["content"][0]["text"].strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw.strip())
        return {
            "diagnosis": parsed.get("diagnosis", diagnosis),
            "reasoning": parsed.get("reasoning", reasoning),
            "practical_next_steps": parsed.get("practical_next_steps", practical_next_steps),
            "accountability": parsed.get("accountability", accountability),
            "llm_enriched": True,
            "llm_error": None,
        }
    except Exception as e:
        return {
            "diagnosis": diagnosis,
            "reasoning": reasoning,
            "practical_next_steps": practical_next_steps,
            "accountability": accountability,
            "llm_enriched": False,
            "llm_error": str(e),
        }


def interpret_analysis(
    result: Dict[str, Any],
    extracted_text: str = "",
    relationship_type: str = "stranger",
    context_note: str = "",
    requested_mode: str = "risk",
    use_llm: bool = False,
) -> Dict[str, Any]:
    out = dict(result or {})
    requested_mode = str(requested_mode or "risk").lower().strip()
    if requested_mode not in {"connection", "risk"}:
        requested_mode = "risk"

    if _risk_override(out):
        out = _risk_copy(out)
        if requested_mode == "connection":
            out["mode_override_note"] = "Connection mode was selected, but stronger safety signals pushed this result into a more protective read."
        out["requested_mode"] = requested_mode
        return out

    if requested_mode == "connection":
        out = _connection_copy(out)
    else:
        out = _risk_copy(out)

    out["requested_mode"] = requested_mode

    if use_llm and extracted_text:
        enriched = _llm_enrich(
            result=result,
            extracted_text=extracted_text,
            presentation_mode=out.get("presentation_mode", requested_mode),
            diagnosis=out.get("diagnosis", ""),
            reasoning=out.get("reasoning", ""),
            practical_next_steps=out.get("practical_next_steps", ""),
            accountability=out.get("accountability", ""),
        )
        out["diagnosis"] = enriched["diagnosis"]
        out["reasoning"] = enriched["reasoning"]
        out["practical_next_steps"] = enriched["practical_next_steps"]
        out["accountability"] = enriched["accountability"]
        out["llm_enriched"] = enriched["llm_enriched"]
        out["llm_error"] = enriched["llm_error"]
    else:
        out["llm_enriched"] = False
        out["llm_error"] = None

    return out