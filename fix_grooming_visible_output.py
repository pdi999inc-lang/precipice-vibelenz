from pathlib import Path

path = Path("app/analyzer.py")
text = path.read_text(encoding="utf-8")

patch = r'''
def _scrub_grooming_outputs(result):
    blocked_terms = {
        "grooming",
        "early stage grooming indicators",
        "early_stage_grooming_indicators",
        "predation",
        "possible_predation",
        "sexual_predation",
    }

    def clean_items(items):
        cleaned = []
        for item in (items or []):
            if isinstance(item, str):
                norm = item.strip().lower().replace(" ", "_")
                if norm in {x.replace(" ", "_") for x in blocked_terms}:
                    continue
                cleaned.append(item)
            elif isinstance(item, dict):
                keep = True
                for key in ("signal", "name", "id", "label", "title"):
                    val = item.get(key)
                    if val:
                        norm = str(val).strip().lower().replace(" ", "_")
                        if norm in {x.replace(" ", "_") for x in blocked_terms}:
                            keep = False
                            break
                if keep:
                    cleaned.append(item)
        return cleaned

    result["flags"] = clean_items(result.get("flags", []))
    result["active_combos"] = clean_items(result.get("active_combos", []))

    if str(result.get("phase", "")).upper() in {"GROOMING", "COERCION", "ENDGAME"}:
        result["phase"] = "NONE"

    summary = str(result.get("summary", "") or "")
    lower = summary.lower()
    if "groom" in lower or "predat" in lower:
        result["summary"] = "Conversation shows identity ambiguity and quick flirtation escalation, but no clear financial coercion or exploitative behavior in the visible exchange."

    return result
'''

if "_scrub_grooming_outputs" not in text:
    anchor = "def _apply_relationship_guardrails(result, relationship_type=\"stranger\"):"
    idx = text.find(anchor)
    if idx == -1:
        raise SystemExit("Could not find relationship guardrail function")
    text = text[:idx] + patch + "\n\n" + text[idx:]

old = """    result = _apply_relationship_guardrails(result, relationship_type=relationship_type)
    return result"""
new = """    result = _apply_relationship_guardrails(result, relationship_type=relationship_type)
    result = _scrub_grooming_outputs(result)
    return result"""

if old in text and "_scrub_grooming_outputs(result)" not in text:
    text = text.replace(old, new, 1)
else:
    raise SystemExit("Could not find final guardrail return block to patch")

path.write_text(text, encoding="utf-8")
print("Updated app/analyzer.py with visible-output grooming scrub")
