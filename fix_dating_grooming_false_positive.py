from pathlib import Path

path = Path("app/main.py")
text = path.read_text(encoding="utf-8")

helper = '''

def _downgrade_false_positive_grooming(result: dict, relationship_type: str) -> dict:
    relationship_type = str(relationship_type or "").lower()
    if relationship_type not in {"dating", "family", "friend", "business"}:
        return result

    flags = [str(x).strip().lower() for x in (result.get("flags") or [])]
    summary = str(result.get("summary") or "").lower()
    phase = str(result.get("phase") or "").upper()
    combos = " ".join(str(x) for x in (result.get("active_combos") or [])).lower()
    evidence_text = " ".join(str(v) for v in (result.get("evidence") or {}).values()).lower()

    combined = " ".join(flags) + " " + summary + " " + combos + " " + evidence_text

    hard_indicators = [
        "money", "gift", "wire", "venmo", "paypal", "cashapp", "bitcoin", "crypto",
        "blackmail", "threat", "coerc", "isolation", "secrecy", "minor", "underage",
        "age gap", "power imbalance", "exploit", "extort", "emergency", "travel fee"
    ]

    soft_pattern = {
        "accidental_contact_opener",
        "platform_migration_early",
        "love_bomb_velocity",
        "verification_avoidance",
    }

    has_hard = any(word in combined for word in hard_indicators)
    only_soft = len(flags) > 0 and set(flags).issubset(soft_pattern)

    if (phase == "GROOMING" or "groom" in combined) and only_soft and not has_hard:
        result["phase"] = "NONE"
        result["vie_action"] = "MONITOR"
        result["risk_score"] = min(int(result.get("risk_score", 0) or 0), 24)
        result["flags"] = ["uncertain_identity", "rapid_flirtation", "needs_verification"]
        result["active_combos"] = []
        result["summary"] = "Conversation shows identity ambiguity and quick flirtation escalation, but no clear financial coercion or exploitative behavior in the visible exchange."

        positive = result.get("positive_signals") or []
        if "reciprocal_engagement" not in positive:
            positive.append("reciprocal_engagement")
        result["positive_signals"] = positive

    return result
'''

if "_downgrade_false_positive_grooming" not in text:
    anchor = 'templates = Jinja2Templates(directory="templates")'
    if anchor not in text:
        raise SystemExit("Could not find insertion point in app/main.py")
    text = text.replace(anchor, anchor + helper, 1)

old = '        result = analyze_text(extracted_text, relationship_type=relationship_type)'
new = '        result = analyze_text(extracted_text, relationship_type=relationship_type)\n        result = _downgrade_false_positive_grooming(result, relationship_type)'

if old in text and "_downgrade_false_positive_grooming(result, relationship_type)" not in text:
    text = text.replace(old, new, 1)
else:
    raise SystemExit("Could not find analyze_text call to patch")

path.write_text(text, encoding="utf-8")
print("Patched app/main.py with dating false-positive downgrade")
