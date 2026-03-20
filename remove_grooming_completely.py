from pathlib import Path

path = Path("app/main.py")
text = path.read_text(encoding="utf-8")

needle = '    result = apply_degradation(result, assessment)'

block = '''
    result = apply_degradation(result, assessment)

    # TEMP HARD BLOCK: remove all grooming language from final output
    blocked_terms = ("groom", "predat")

    def _contains_blocked(value):
        return any(term in str(value).lower() for term in blocked_terms)

    # Remove grooming phase entirely
    if _contains_blocked(result.get("phase", "")):
        result["phase"] = "NONE"

    # Remove grooming-like flags
    cleaned_flags = []
    for item in result.get("flags", []) or []:
        if not _contains_blocked(item):
            cleaned_flags.append(item)

    if cleaned_flags != (result.get("flags", []) or []):
        result["flags"] = cleaned_flags

    # Remove grooming-like active combos
    cleaned_combos = []
    for item in result.get("active_combos", []) or []:
        if not _contains_blocked(item):
            cleaned_combos.append(item)

    if cleaned_combos != (result.get("active_combos", []) or []):
        result["active_combos"] = cleaned_combos

    # Rewrite summary if it contains grooming language
    if _contains_blocked(result.get("summary", "")):
        result["summary"] = "Conversation shows identity ambiguity or flirtation, but no grooming label is being used in the current beta output."

    # Add fallback safer labels if everything got stripped
    if not result.get("flags"):
        result["flags"] = ["uncertain_identity", "rapid_flirtation", "needs_verification"]

    # Keep action conservative but not extreme
    if str(result.get("vie_action", "")).upper() == "BLOCK":
        result["vie_action"] = "MONITOR"

    try:
        result["risk_score"] = min(int(result.get("risk_score", 0) or 0), 24)
    except Exception:
        result["risk_score"] = 24
'''

if needle not in text:
    raise SystemExit("Could not find apply_degradation line in app/main.py")

if "TEMP HARD BLOCK: remove all grooming language from final output" not in text:
    text = text.replace(needle, block, 1)

path.write_text(text, encoding="utf-8")
print("Inserted hard grooming-removal block into app/main.py")
