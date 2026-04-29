from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List

import httpx

logger = logging.getLogger("vibelenz.interpreter")

_VALID_GENDERS = {"female", "male", "unknown"}


def _clean(items: List[str]) -> List[str]:
    return [str(x).strip() for x in (items or []) if str(x).strip()]


def _has(items: List[str], value: str) -> bool:
    return value in _clean(items)


def _norm_gender(other_gender: str) -> str:
    g = str(other_gender or "unknown").lower().strip()
    return g if g in _VALID_GENDERS else "unknown"


def _human_label(primary_label: str, lane: str, domain_mode: str) -> str:
    mapping = {
        "playful_reengagement":           "playful reconnection",
        "light_sexual_reciprocity":       "light sexual reciprocity",
        "warm_receptivity":               "warm receptivity",
        "confusion_then_repair":          "confusion that clears",
        "casual_flirtation":              "casual flirtation",
        "low_information_neutral":        "low-stakes interaction",
        "routine_host_message":           "routine logistics",
        "transactional_extraction_pattern": "transactional risk pattern",
        "pressure_with_boundary_violation": "pressure pattern",
        "high_intent_mutual":             "mutual high intent",
        "fear_driven_urgency":            "fear-driven urgency",
        "mixed_intent_genuine":           "genuine mixed signals",
        "fast_escalation_noncoercive":    "fast escalation",
        "relationship_context":           "established relationship",
        "routine_message":                "low-stakes interaction",
        "mixed_intent":                   "mixed intent",
    }
    return mapping.get(primary_label, primary_label.replace("_", " "))


_FINANCIAL_SIGNALS = frozenset({
    "trust_calibration_small_ask",
    "vulnerability_narrative_early",
    "financial_extraction",
    "money_request",
    "gift_card_request",
    "wire_transfer_request",
})


def _has_financial_signals(concern_signals: List[str]) -> bool:
    """True if any concern signal indicates financial extraction attempt."""
    cs = _clean(concern_signals)
    return any(
        s in _FINANCIAL_SIGNALS or "financial" in s or "extraction" in s
        for s in cs
    )


def _social_tone(result: Dict[str, Any]) -> str:
    positives = _clean(result.get("positive_signals", []))
    primary_label = str(result.get("primary_label", "low_information_neutral"))
    # Financial extraction signals override tone — never call this "low-stakes"
    if _has_financial_signals(result.get("concern_signals", [])):
        return "financial request detected — not a routine interaction"
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
        "playful_reengagement":     "good receptivity",
        "light_sexual_reciprocity": "clear playful interest",
        "warm_receptivity":         "positive openness",
        "confusion_then_repair":    "improving energy",
        "casual_flirtation":        "light interest",
        "high_intent_mutual":       "strong mutual interest",
        "fear_driven_urgency":      "high but pressured",
        "mixed_intent_genuine":     "moderate, still developing",
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
        if domain_mode == "housing_rental":
            diagnosis = "This rental interaction shows deliberate pressure patterns — legitimate landlords and agents do not operate this way."
            reasoning = "Legitimate rentals do not require commitment before a showing, create artificial urgency around availability, or gate identity verification behind payment. Those are extraction patterns designed to move you toward sending money or documents before you can independently confirm anything."
            next_steps = "Stop. Verify independently — search the address, call the county assessor, confirm the listing on the original platform. Do not send money, a deposit, or personal documents until you have physically met someone at the property."
            accountability = "Polite tone and professional language do not change what the process is asking you to do. A well-scripted pressure tactic is still a pressure tactic."
        else:
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


def _copy_playful_reengagement(g: str) -> Dict[str, str]:
    if g == "female":
        return {
            "diagnosis": "She was confused, then embarrassed, then she came back around — and that arc is more telling than if it had gone smoothly.",
            "reasoning": "She probably did not remember clearly at first, then pieces came back, and once she placed you she settled down. This does not look like a calculated rejection — it looks like someone who was scatterbrained, embarrassed, and trying to recover. The energy after the confusion cleared is not what rejection looks like. A woman who is genuinely turned off does not lean back in. Honest read: still open to talking at 60 to 70 percent. Genuinely into you in a stable way, more like 35 to 45.",
            "practical_next_steps": "Do not make this heavier than it is. Do not ask her why she reacted that way or what she meant. Treat it like a slightly awkward reconnection that already repaired itself. Something like 'You're good. I'll allow the new phone excuse.' That keeps it easy and puts you back in control of the tone.",
            "accountability": "Your bigger risk is mixing modes. You had playful energy going and switched into something heavier. Flirt if you are flirting. Go deep if you are going deep. Mixing them weakens both. She does not look uninterested — she looks disorganized. You still have room.",
        }
    elif g == "male":
        return {
            "diagnosis": "He got confused, then pulled back, then came back around — that sequence tells you more than the confusion itself.",
            "reasoning": "Men rarely re-engage after confusion unless there is something pulling them back. The fact that he recovered and came back in is the data point. It was not a calculated move — it looks like he was caught off-guard, reset, and decided to come back. That is not withdrawal behavior. Honest read: still open at 60 to 70 percent. Solidly into it in a stable way, more like 35 to 45.",
            "practical_next_steps": "Do not rehash the awkward moment. He already moved past it. Keep the next message light — acknowledge the weirdness without dwelling. Something brief that signals you are not rattled by it.",
            "accountability": "Do not overanalyze the confusion when the simpler read is that he was caught off-guard and chose to come back. That is the signal. Stop making it heavier than it is.",
        }
    else:
        return {
            "diagnosis": "There was confusion, then some embarrassment, then a real comeback — that arc is more telling than if it had gone smoothly.",
            "reasoning": "The rough opening is not the story. People who are checked out do not bother coming back. Once the confusion cleared, the energy shifted toward warmth — that is what matters. Honest read: still open at 60 to 70 percent. Solidly invested in a stable way, more like 35 to 45.",
            "practical_next_steps": "Do not drag the awkward moment back in. They already moved past it — follow their lead. Keep it light and acknowledge the weirdness briefly without making it a topic.",
            "accountability": "Stop overanalyzing the confusion when the obvious read is simpler: embarrassed, recovered, still open. You are not locked into the worst version of how this started.",
        }


def _copy_light_sexual_reciprocity(g: str) -> Dict[str, str]:
    if g == "female":
        return {
            "diagnosis": "There is real flirtatious energy here and she is matching it, not just tolerating it.",
            "reasoning": "This is not politeness. She is leaning in. The reciprocal tone is visible — she is not deflecting, going cold, or redirecting. Women who are not interested do not sustain this kind of energy. That is the signal that matters more than anything else in an early exchange.",
            "practical_next_steps": "Stay with it. Let the energy breathe. The moment you start explaining yourself or pivoting into serious mode, you lose the thread.",
            "accountability": "Do not talk yourself out of chemistry that is already working.",
        }
    elif g == "male":
        return {
            "diagnosis": "There is real flirtatious energy here and he is matching it, not just being polite.",
            "reasoning": "He is reciprocating, not deflecting. Men who are not interested do not sustain this kind of energy — they go flat or vague. The tone here is not passive. That reciprocity is the signal.",
            "practical_next_steps": "Stay in the energy. Do not pivot to something heavier or more serious before this thread runs its course.",
            "accountability": "Do not overcomplicate something that is already working.",
        }
    else:
        return {
            "diagnosis": "There is real flirtatious energy here and they are matching it, not just tolerating it.",
            "reasoning": "The reciprocal tone is visible — no deflecting, redirecting, or going cold. That is the signal that matters most in an early exchange.",
            "practical_next_steps": "Stay with it. The moment you pivot into something heavier, you risk losing the thread.",
            "accountability": "Do not talk yourself out of chemistry that is already working.",
        }


def _copy_warm_receptivity(g: str) -> Dict[str, str]:
    if g == "female":
        return {
            "diagnosis": "She is open and engaged — not guarded, not pulling back.",
            "reasoning": "What stands out is not intensity, it is the absence of resistance. She is not looking for an exit. That openness is quiet but real, and it is a better signal than surface enthusiasm that disappears the moment things get less easy.",
            "practical_next_steps": "Keep the tone easy and person-focused. Let consistency do the work — not pressure, not grand gestures.",
            "accountability": "Warm does not mean locked in. Do not skip the part where you actually build something.",
        }
    elif g == "male":
        return {
            "diagnosis": "He is open and engaged — not guarded, not pulling back.",
            "reasoning": "What stands out is not intensity, it is the absence of resistance. He is not looking for an exit. Men who are disinterested disengage — they go short, vague, or stop initiating. None of that is happening here.",
            "practical_next_steps": "Keep the tone easy and consistent. Do not force pace — let the warmth develop naturally.",
            "accountability": "Warm does not mean locked in. Do not skip the part where you actually build something.",
        }
    else:
        return {
            "diagnosis": "They are open and engaged — not guarded, not pulling back.",
            "reasoning": "What stands out is not intensity, it is the absence of resistance. There is no sign of an exit being sought. That openness is quiet but real.",
            "practical_next_steps": "Keep the tone easy and person-focused. Let consistency do the work from here.",
            "accountability": "Warm does not mean locked in. Do not skip the part where you actually build something.",
        }


def _copy_confusion_then_repair(g: str) -> Dict[str, str]:
    if g == "female":
        return {
            "diagnosis": "It started awkward, but she worked to fix it — and that is the part that actually matters.",
            "reasoning": "The rough opening is not the story. Women who are checked out do not bother repairing the energy. The confusion cleared and she came back. That means there was something worth recovering in her mind. She is not locked in and she is not fully consistent, but she is not shutting you down either.",
            "practical_next_steps": "Do not drag the awkward moment back in. She already moved past it — follow her lead. Keep it light.",
            "accountability": "Stop overanalyzing her confusion when the obvious read is simpler: she was embarrassed, she recovered, and she is still open.",
        }
    elif g == "male":
        return {
            "diagnosis": "It started awkward, but he worked to fix it — and that is the part that actually matters.",
            "reasoning": "The rough opening is not the story. Men who are not interested do not spend energy repairing. The confusion cleared and he came back — that is intentional. Something pulled him back in.",
            "practical_next_steps": "Do not revisit the awkward opening. He already moved past it. Keep the next move light and forward.",
            "accountability": "Stop reading the confusion as rejection when the recovery is the actual data. He came back. That is the signal.",
        }
    else:
        return {
            "diagnosis": "It started awkward, but there was a real effort to fix it — and that is what actually matters.",
            "reasoning": "People who are checked out do not bother repairing the energy. The confusion cleared and they came back. That means there was something worth recovering.",
            "practical_next_steps": "Do not drag the awkward moment back in. They already moved past it. Follow their lead and keep it light.",
            "accountability": "Stop overanalyzing the confusion when the recovery is the actual story. They came back. That is what counts.",
        }


def _copy_casual_flirtation(g: str) -> Dict[str, str]:
    if g == "female":
        return {
            "diagnosis": "Light, easy, and going in the right direction.",
            "reasoning": "Nothing here is heavy or loaded. The tone is playful and she is participating — not deflecting, not going cold. What is absent matters as much as what is present.",
            "practical_next_steps": "Keep it light. Do not make it heavier than it needs to be right now.",
            "accountability": "Not every good thing needs to be analyzed into the ground. Sometimes easy is just easy.",
        }
    elif g == "male":
        return {
            "diagnosis": "Light, easy, and going in the right direction.",
            "reasoning": "Nothing here is heavy or loaded. The tone is playful and he is participating — not deflecting, not going flat. What is absent matters as much as what is present.",
            "practical_next_steps": "Keep it light. Do not make it heavier than it needs to be right now.",
            "accountability": "Not every good thing needs to be analyzed into the ground. Sometimes easy is just easy.",
        }
    else:
        return {
            "diagnosis": "Light, easy, and going in the right direction.",
            "reasoning": "Nothing here is heavy or loaded. The tone is playful and they are participating — no deflection, no cold drop. What is absent matters as much as what is present.",
            "practical_next_steps": "Keep it light. Do not make it heavier than it needs to be.",
            "accountability": "Not every good thing needs to be analyzed into the ground. Sometimes easy is just easy.",
        }


def _copy_high_intent_mutual(g: str, out: Dict[str, Any]) -> Dict[str, str]:
    concern_signals = _clean(out.get("concern_signals", []))
    fear_driven = "fear_driven_urgency" in concern_signals
    goal_sub = "goal_substitution" in concern_signals
    pressure_present = "pressure_present" in concern_signals
    diagnosis = "Both people are showing up with real intent — this conversation has weight to it."
    reasoning = "The alignment here is not surface-level. There is shared vision, reciprocal investment, and both people are being direct about what they want. That combination is rarer than it looks and worth taking seriously."
    if fear_driven or goal_sub:
        if g == "female":
            reasoning += " That said, some of the urgency on her side reads as fear-driven rather than vision-driven. She may be operating from scarcity — avoiding an outcome rather than building toward one. That does not disqualify the connection, but the timeline pressure is hers, not necessarily yours."
        elif g == "male":
            reasoning += " That said, some of the urgency on his side reads as fear-driven rather than vision-driven. He may be operating from scarcity — avoiding an outcome rather than building toward one. That does not disqualify the connection, but the timeline pressure is his, not necessarily yours."
        else:
            reasoning += " That said, some of the urgency reads as fear-driven rather than vision-driven. Someone here may be operating from scarcity — avoiding an outcome rather than building toward one. Enter this clearly."
    next_steps = "The conversation has done its job. The next move is a real-world meeting — not another exchange, not more rapport-building. Ask directly. Something grounded: 'I think we have covered enough ground — are you free this week?'"
    if pressure_present:
        accountability = "The energy is compelling and they are moving fast. Make sure you are choosing this clearly rather than getting swept into their timeline. Fast is fine if it is mutual. Fast because someone is scared is different."
    else:
        accountability = "You have the alignment. The only way this stalls now is if you stay in the conversation instead of converting it. Get off the app."
    return {"diagnosis": diagnosis, "reasoning": reasoning, "practical_next_steps": next_steps, "accountability": accountability}


def _copy_fear_driven_urgency(g: str) -> Dict[str, str]:
    if g == "female":
        subj, poss = "She knows", "her"
    elif g == "male":
        subj, poss = "He knows", "his"
    else:
        subj, poss = "They know", "their"
    return {
        "diagnosis": f"{subj} what they want — but some of that urgency is about avoiding something, not just building toward it.",
        "reasoning": f"The timeline pressure, the early ultimatums, the substitution of outcomes — these are not red flags exactly, but they are signals worth reading clearly. A person operating from scarcity will move fast, commit fast, and may accept a suboptimal match to resolve the fear. The connection can still be real. But the pressure is coming from {poss} internal clock, not from what is actually between you yet.",
        "practical_next_steps": f"Do not match {poss} urgency. Stay grounded. If the connection is real it will hold at your pace too. If it only works at their pace, that tells you something important.",
        "accountability": f"The risk here is not {poss} intent — it is your clarity. Make sure you are making an active choice, not just going along because the energy is strong.",
    }


def _copy_mixed_intent_genuine(g: str) -> Dict[str, str]:
    return {
        "diagnosis": "The signals are mixed, but not in a way that reads as calculated — this just needs more time.",
        "reasoning": "There is enough positive signal here to take seriously, but not enough yet to make a confident read in either direction. That is not a problem — it is just where this conversation actually is. Pushing for a conclusion before the data supports it will only produce a wrong one.",
        "practical_next_steps": "One or two more direct exchanges will tell you more than any analysis of what you have now. Ask something that requires a real answer — not small talk.",
        "accountability": "Mixed does not mean bad. It means early. Stop trying to resolve ambiguity that has not had time to resolve itself.",
    }


def _copy_relationship_context(g: str) -> Dict[str, str]:
    if g == "female":
        return {
            "diagnosis": "You know each other — this is not a stranger dynamic, so the normal early-read rules do not apply.",
            "reasoning": "There is an established baseline here. What matters is whether this exchange tracks with how she normally shows up, or departs from it. A single screenshot is a narrow window into an ongoing dynamic. Your broader context is more informative than any isolated message.",
            "practical_next_steps": "Do not analyze this like a new interaction — you already have a pattern to compare against. If this feels consistent with her, it probably is. If something feels different, that departure is the data worth paying attention to.",
            "accountability": "The risk with people you know is normalizing gradual shifts. If the tone, availability, or effort level is consistently different from the established baseline, trust that read over the individual message.",
        }
    elif g == "male":
        return {
            "diagnosis": "You know each other — this is not a stranger dynamic, so the normal early-read rules do not apply.",
            "reasoning": "There is an established baseline here. What matters is whether this exchange tracks with how he normally shows up, or departs from it. A single screenshot is a narrow window into an ongoing dynamic. Your broader context is more informative than any isolated message.",
            "practical_next_steps": "Do not analyze this like a new interaction — you already have a pattern to compare against. If this feels consistent with him, it probably is. If something feels different, that departure is the data worth paying attention to.",
            "accountability": "The risk with people you know is normalizing gradual shifts. If the tone, availability, or effort level is consistently different from the established baseline, trust that read over the individual message.",
        }
    else:
        return {
            "diagnosis": "You know each other — this is not a stranger dynamic, so the normal early-read rules do not apply.",
            "reasoning": "There is an established baseline between you. A single exchange is a narrow window into an ongoing dynamic. What matters is whether this tracks with the pattern you already know or departs from it.",
            "practical_next_steps": "Compare this to the broader pattern, not just the screenshot. Your existing context is more informative than any snapshot analysis.",
            "accountability": "The risk in known relationships is normalizing gradual shifts. If something consistently feels different from the established baseline, trust that read.",
        }


def _copy_mixed_intent(g: str, out: Dict[str, Any]) -> Dict[str, str]:
    concern_signals = _clean(out.get("concern_signals", []))
    has_pressure = any("pressure" in s for s in concern_signals)
    return {
        "diagnosis": "The signals here are pointing in more than one direction — not a clear positive read, not a clear red flag.",
        "reasoning": (
            "Mixed intent is not a problem in itself — it just means the picture is not resolved yet. "
            + ("There are some friction signals here worth tracking as this develops. " if has_pressure else "Nothing here looks deliberately deceptive, but the motivation is not yet clear. ")
            + "Trying to force a confident read from ambiguous data usually produces a wrong one."
        ),
        "practical_next_steps": "Do not try to resolve this on the analysis side. One or two direct exchanges will produce more clarity than any interpretation of what you already have. Ask something that requires a genuine answer — not small talk.",
        "accountability": "Mixed does not mean bad — it means more information is needed. Stop trying to reach a conclusion before the picture has had time to form.",
    }


def _copy_generic(g: str, out: Dict[str, Any]) -> Dict[str, str]:
    concern_signals = _clean(out.get("concern_signals", []))
    has_pressure = any("pressure" in s for s in concern_signals)
    return {
        "diagnosis": "This is a real human interaction — low stakes, not a threat, just still early.",
        "reasoning": (
            "It reads like a normal exchange between two people who are still figuring out the dynamic. That is not a bad thing — it just means the picture is not complete yet. "
            + ("There are some early friction signals worth keeping an eye on as things develop. " if has_pressure else "Nothing here points to strong pressure or bad intent. ")
            + "A few more exchanges will tell you more than any analysis of what you already have."
        ),
        "practical_next_steps": "Treat it lightly. Let the next few exchanges do the work instead of trying to force a conclusion from limited data.",
        "accountability": "Stop trying to solve it before it has had time to develop. You do not have enough information yet to make a hard call — and that is okay.",
    }


def _connection_copy(out: Dict[str, Any], other_gender: str = "unknown") -> Dict[str, Any]:
    primary_label = str(out.get("primary_label", "low_information_neutral"))
    g = _norm_gender(other_gender)
    dispatch = {
        "playful_reengagement":     lambda: _copy_playful_reengagement(g),
        "light_sexual_reciprocity": lambda: _copy_light_sexual_reciprocity(g),
        "warm_receptivity":         lambda: _copy_warm_receptivity(g),
        "confusion_then_repair":    lambda: _copy_confusion_then_repair(g),
        "casual_flirtation":        lambda: _copy_casual_flirtation(g),
        "high_intent_mutual":       lambda: _copy_high_intent_mutual(g, out),
        "fear_driven_urgency":      lambda: _copy_fear_driven_urgency(g),
        "mixed_intent_genuine":     lambda: _copy_mixed_intent_genuine(g),
        "relationship_context":     lambda: _copy_relationship_context(g),
        "mixed_intent":             lambda: _copy_mixed_intent(g, out),
    }
    copy = dispatch.get(primary_label, lambda: _copy_generic(g, out))()
    out["presentation_mode"] = "connection"
    out["mode_title"] = "Connection Analysis"
    out["mode_tagline"] = "Warm read on chemistry, receptivity, emotional movement, and what to do next."
    out["human_label"] = _human_label(str(out.get("primary_label", "")), str(out.get("lane", "")), str(out.get("domain_mode", "")))
    out["diagnosis"] = copy["diagnosis"]
    out["reasoning"] = copy["reasoning"]
    out["practical_next_steps"] = copy["practical_next_steps"]
    out["accountability"] = copy["accountability"]
    out["social_tone"] = _social_tone(out)
    out["interest_summary"] = _interest_summary(out)
    # When financial extraction signals are present: suppress positive signals and surface warning.
    # Positive signal chips create false reassurance when a financial ask is present.
    if _has_financial_signals(out.get("concern_signals", [])):
        out["positive_signals"] = []
        out["mode_override_note"] = "Financial extraction signals were detected. Connection mode framing does not soften what the content is showing — read the analysis below as a risk read."
    else:
        out["mode_override_note"] = ""
    return out


def _llm_enrich(result, extracted_text, presentation_mode, diagnosis, reasoning, practical_next_steps, accountability):
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {"diagnosis": diagnosis, "reasoning": reasoning, "practical_next_steps": practical_next_steps, "accountability": accountability, "llm_enriched": False, "llm_error": "no_api_key"}
    primary_label = result.get("primary_label", "unknown")
    concern_signals = result.get("concern_signals", [])
    positive_signals = result.get("positive_signals", [])
    risk_score = result.get("risk_score", None)
    lane = result.get("lane", "BENIGN")
    if presentation_mode == "connection":
        system_prompt = "You are VibeLenz, a conversation dynamics analyst. Your job is to give the user a precise, honest read on what is actually happening in their dating conversation — not what might be happening, not what could charitably be assumed, but what the text actually shows.\n\nYOUR ROLE: Write like a sharp, honest friend who has read every message. Be specific. Be direct. Do not hedge. Do not soften what is real. Do not manufacture warmth where the conversation does not support it.\n\nHOW TO ANALYZE: Before writing, identify who is the user and who is the other person. Map what each person actually said and did. Identify the dynamic: who is pursuing, who is redirecting, who is engaging, who is filtering. Look for gaps between what someone says and what they do. Only name signals directly supported by specific text.\n\nWHAT TO WATCH FOR:\n- Position reversals: someone says X then under mild pushback acts as if they said Y\n- Value filtering disguised as advice: repeated prescriptive statements about what the user should do differently signal a standard being communicated, not helpfulness\n- Competence challenges: telling someone to ask a sister, friend, or anyone else for dating advice signals they see the user as socially unequipped — name it as such, not as coaching\n- Effort signaling: rejecting a reasonable offer not on logistics grounds but on effort or thoughtfulness grounds means they are communicating what level of pursuit they require\n- Engagement vs redirection: someone who responds to every message with advice instead of answering the actual question is not engaging — they are redirecting\n- The difference between I cannot meet today and your offer is not good enough — these are different signals and must not be conflated\n- Lure and pivot: a suggestive or flattering opener used to elicit a compliance response, then a transactional request lands immediately after. Example: 'Want to make my night' followed hours later by 'can you Uber Eats me breakfast' — the opener is bait designed to get agreement in place before the ask arrives. Name this pattern directly if you see it.\n- Commitment trap: using the target's earlier agreement ('Sure, what can I do to help') as implied consent or social contract to justify a subsequent request. If someone's ask only makes sense because of a prior open-ended agreement they manufactured, name that.\n\nPOSITIVE SIGNALS — EVIDENCE REQUIRED: Only include a positive signal if you can point to a specific moment in the conversation that demonstrates it. If you cannot cite the text, do not include the signal.\n- reciprocal_engagement: both people asking questions and responding — not if one person is only advising\n- boundary_respect: someone explicitly accepted a stated limit — not applicable if no limit was tested\n- meeting_willingness: someone explicitly said yes to meeting or made a concrete counter-offer — not if they only gave advice about how to ask better\n- mutual_joking: both people visibly joking or bantering — a softening lol on criticism does not count\n- patient_pacing: someone genuinely giving space without raising the bar — not if they are prescribing higher effort standards\n- no_coercion: zero you need to or ultimatum framing — if someone said you need to more than once, this signal is not present\n- transparent_intentions: someone was clear about what they want — only applicable to that specific person\n- no_financial_topics: only list if financial topics were a realistic possibility given the context\n\nWHAT NOT TO DO:\n- Do not introduce people who are not in the conversation\n- Do not assume positive intent where the text shows something different\n- Do not list positive signals to balance out negative ones — accuracy matters more than balance\n- Do not use: danger, bad intent, threat, toxic, narcissist\n- Do not hedge with it could be or perhaps — make a call based on what is there\n\nFIELD VOICE RULES — follow exactly:\n- diagnosis: One to three sentences. What is actually happening. Third-person observation.\n- reasoning: Explain the read using specific moments from the text. Still analytical, but personal — 'you' is fine here.\n- practical_next_steps: Direct instructions to the user. Second person. 'Do this. Watch for that. Do not do this.'\n- accountability: A direct second-person challenge to the user. Address them as 'you'. Do NOT describe the analysis or restate signals — instead name the specific rationalization the user is most likely to make right now and challenge it. Example: 'You are going to tell yourself the first message was warm enough to offset the second. It is not. What someone asks you for on a second message is information regardless of how the first one felt.' Keep it short. One to three sentences maximum.\n\nReturn ONLY a JSON object with keys: diagnosis, reasoning, practical_next_steps, accountability. No preamble. No markdown. Raw JSON only."
        relationship_context = f"Relationship type submitted: {result.get('relationship_type', 'stranger')}. " if result.get('relationship_type') and result.get('relationship_type') != 'stranger' else ''
        if relationship_context:
            relationship_context += "Do not frame output around dating interest, romantic pursuit, or whether this person is a dating prospect. Frame around the actual relationship dynamic submitted."
        user_prompt = f"CONVERSATION:\n{extracted_text}\n\n{relationship_context}\nDETECTED SIGNALS:\nPositive: {json.dumps(positive_signals)}\nConcern: {json.dumps(concern_signals)}\nPrimary label: {primary_label}\nLane: {lane}\n\nDETERMINISTIC DRAFT:\nDiagnosis: {diagnosis}\nReasoning: {reasoning}\nNext steps: {practical_next_steps}\nAccountability: {accountability}\n\nRewrite the draft to be more specific to this actual conversation. Return raw JSON only."
    else:
        system_prompt = "You are VibeLenz, a conversation safety analyst. Give the user a clear, honest read on risk signals. Be direct and specific. Do not catastrophize. Do not minimize. Return ONLY a JSON object with keys: diagnosis, reasoning, practical_next_steps, accountability. No preamble. No markdown. Raw JSON only."
        user_prompt = f"CONVERSATION:\n{extracted_text}\n\nDETECTED SIGNALS:\nRisk signals: {json.dumps(result.get('key_signals', []))}\nConcern signals: {json.dumps(concern_signals)}\nPrimary label: {primary_label}\nLane: {lane}\nRisk score: {risk_score}\n\nDETERMINISTIC DRAFT:\nDiagnosis: {diagnosis}\nReasoning: {reasoning}\nNext steps: {practical_next_steps}\nAccountability: {accountability}\n\nRewrite the draft to be more specific to this actual conversation. Return raw JSON only."
    try:
        response = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-haiku-4-5-20251001", "max_tokens": 1024, "system": system_prompt, "messages": [{"role": "user", "content": user_prompt}]},
            timeout=15.0,
        )
        response.raise_for_status()
        data = response.json()
        raw = data["content"][0]["text"].strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw.strip())
        return {"diagnosis": parsed.get("diagnosis", diagnosis), "reasoning": parsed.get("reasoning", reasoning), "practical_next_steps": parsed.get("practical_next_steps", practical_next_steps), "accountability": parsed.get("accountability", accountability), "llm_enriched": True, "llm_error": None}
    except Exception as e:
        return {"diagnosis": diagnosis, "reasoning": reasoning, "practical_next_steps": practical_next_steps, "accountability": accountability, "llm_enriched": False, "llm_error": str(e)}


def interpret_analysis(
    result: Dict[str, Any],
    extracted_text: str = "",
    relationship_type: str = "stranger",
    other_gender: str = "unknown",
    context_note: str = "",
    requested_mode: str = "risk",
    use_llm: bool = False,
) -> Dict[str, Any]:
    out = dict(result or {})
    requested_mode = str(requested_mode or "risk").lower().strip()
    if requested_mode not in {"connection", "risk"}:
        requested_mode = "risk"
    other_gender = _norm_gender(other_gender)
    if _risk_override(out):
        out = _risk_copy(out)
        if requested_mode == "connection":
            out["mode_override_note"] = "Connection mode was selected, but stronger safety signals pushed this result into a more protective read."
        out["requested_mode"] = requested_mode
        return out
    if requested_mode == "connection":
        out = _connection_copy(out, other_gender=other_gender)
    else:
        out = _risk_copy(out)
    out["requested_mode"] = requested_mode
    if use_llm and extracted_text:
        result["relationship_type"] = relationship_type
        enriched = _llm_enrich(result=result, extracted_text=extracted_text, presentation_mode=out.get("presentation_mode", requested_mode), diagnosis=out.get("diagnosis", ""), reasoning=out.get("reasoning", ""), practical_next_steps=out.get("practical_next_steps", ""), accountability=out.get("accountability", ""))
        out["diagnosis"] = enriched["diagnosis"]
        out["reasoning"] = enriched["reasoning"]
        out["practical_next_steps"] = enriched["practical_next_steps"]
        out["accountability"] = enriched["accountability"]
        out["llm_enriched"] = enriched["llm_enriched"]
        out["llm_error"] = enriched["llm_error"]
    else:
        out["llm_enriched"] = False
        out["llm_error"] = None

    # Sanitize forward-looking contrast language — strip failure framing
    _bad_phrases = ["initial analysis failed", "analysis failed", "the initial read failed", "first pass failed"]
    for _field in ("diagnosis", "reasoning", "practical_next_steps", "accountability"):
        _val = out.get(_field, "") or ""
        for _phrase in _bad_phrases:
            if _phrase in _val.lower():
                out[_field] = _val.replace(_phrase, "the first read was limited").replace(_phrase.capitalize(), "The first read was limited")
    return out










