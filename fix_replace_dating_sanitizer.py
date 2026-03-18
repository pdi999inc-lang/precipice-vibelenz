from pathlib import Path
import re

path = Path("app/main.py")
text = path.read_text(encoding="utf-8")

new_func = '''
def _downgrade_false_positive_grooming(result: dict, relationship_type: str) -> dict:
    relationship_type = str(relationship_type or "").lower()
    if relationship_type not in {"dating", "family", "friend", "business"}:
        return result

    flags = result.get("flags") or []
    active_combos = result.get("active_combos") or []
    evidence = result.get("evidence") or {}
    summary = str(result.get("summary") or "")
    phase = str(result.get("phase") or "").upper()

    def norm_text(x):
        return str(x).strip().lower()

    norm_flags = [norm_text(x) for x in flags]
    combos_text = " ".join(norm_text(x) for x in active_combos)
    evidence_text = " ".join(norm_text(v) for v in evidence.values()) if isinstance(evidence, dict) else norm_text(evidence)
    combined = " ".join(norm_flags) + " " + combos_text + " " + evidence_text + " " + summary.lower() + " " + phase.lower()

    hard_indicators = [
        "money", "gift card", "wire", "paypal", "venmo", "cashapp", "bitcoin", "crypto",
        "blackmail", "threat", "coerc", "isolation", "secrecy", "minor", "underage",
        "age gap", "power imbalance", "extort", "exploit", "emergency", "travel fee",
        "dependency", "conditioning", "repeated manipulation"
    ]

    soft_flags = {
        "accidental_contact_opener",
        "platform_migration_early",
        "love_bomb_velocity",
        "verification_avoidance",
    }

    has_hard = any(word in combined for word in hard_indicators)
    has_grooming_surface = (
        phase == "GROOMING" or
        "groom" in combined or
        "predat" in combined or
        "romance scam early stage" in combined
    )

    flags_are_soft_only = len(norm_flags) > 0 and set(norm_flags).issubset(soft_flags)

    if has_grooming_surface and flags_are_soft_only and not has_hard:
        result["phase"] = "NONE"
        result["vie_action"] = "MONITOR"
        try:
            result["risk_score"] = min(int(result.get("risk_score", 0) or 0), 24)
        except Exception:
            result["risk_score"] = 24

        result["flags"] = ["uncertain_identity", "rapid_flirtation", "needs_verification"]
        result["active_combos"] = []
        result["summary"] = "Conversation shows identity ambiguity and quick flirtation escalation, but no clear financial coercion or exploitative behavior in the visible exchange."

        positive = result.get("positive_signals") or []
        if "reciprocal_engagement" not in positive:
            positive.append("reciprocal_engagement")
        result["positive_signals"] = positive

    return result
'''

pattern = r"def _downgrade_false_positive_grooming\(result: dict, relationship_type: str\) -> dict:\n(?:    .*?\n)+?(?=\n\S)"
new_text, count = re.subn(pattern, new_func + "\n", text, count=1, flags=re.DOTALL)

if count != 1:
    raise SystemExit("Could not replace _downgrade_false_positive_grooming function")

path.write_text(new_text, encoding="utf-8")
print("Replaced _downgrade_false_positive_grooming in app/main.py")
