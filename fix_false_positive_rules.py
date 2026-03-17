from pathlib import Path

path = Path("app/analyzer.py")
text = path.read_text(encoding="utf-8")

marker = "def analyze_text("
insert_code = '''

ROMANCE_HARD_GROOMING_SIGNALS = {
    "target_vulnerability_exploitation",
    "age_power_imbalance",
    "secrecy_encouragement",
    "boundary_conditioning",
    "dependency_shaping",
    "isolation_from_others",
    "coercive_sexual_progression",
    "repeated_manipulation_over_time",
}

ROMANCE_SCAM_HARD_SIGNALS = {
    "financial_ask_escalation",
    "money_request",
    "gift_lure_fee_extraction",
    "emergency_narrative",
    "off_platform_migration",
    "verification_refusal",
    "identity_inconsistency",
    "repeated_contradiction",
    "resource_extraction",
}

LOW_SEVERITY_RAPID_INTIMACY = {
    "love_bomb_velocity",
    "fast_sexual_tone",
    "rapid_flirtation",
    "sexual_forwardness",
    "accelerated_intimacy",
    "accidental_contact_opener",
    "uncertain_identity",
}

RECIPROCAL_DAMPENERS = {
    "mutual_joking",
    "reciprocal_flirting",
    "back_and_forth_engagement",
    "no_fear_discomfort",
    "no_coercion",
    "no_resource_extraction",
    "adult_consensual_tone",
}

def _norm_signal_name(x):
    return str(x).strip().lower().replace(" ", "_")

def _extract_signal_names(result):
    names = set()

    for item in result.get("flags", []) or []:
        if isinstance(item, str):
            names.add(_norm_signal_name(item))
        elif isinstance(item, dict):
            for key in ("signal", "name", "id", "label"):
                if item.get(key):
                    names.add(_norm_signal_name(item[key]))

    for item in result.get("active_combos", []) or []:
        if isinstance(item, str):
            names.add(_norm_signal_name(item))
        elif isinstance(item, dict):
            for key in ("signal", "name", "id", "label"):
                if item.get(key):
                    names.add(_norm_signal_name(item[key]))

    for item in result.get("positive_signals", []) or []:
        if isinstance(item, str):
            names.add(_norm_signal_name(item))
        elif isinstance(item, dict):
            for key in ("signal", "name", "id", "label"):
                if item.get(key):
                    names.add(_norm_signal_name(item[key]))

    return names

def _set_summary(result, text):
    result["summary"] = text
    return result

def _ensure_label(result, label):
    labels = result.get("labels", []) or []
    if label not in labels:
        labels.append(label)
    result["labels"] = labels
    return result

def _remove_label(result, label):
    labels = [x for x in (result.get("labels", []) or []) if str(x).lower() != label.lower()]
    result["labels"] = labels
    return result

def _cap_risk(result, cap):
    try:
        score = int(result.get("risk_score", 0))
    except Exception:
        score = 0
    result["risk_score"] = min(score, cap)
    return result

def _set_action_at_most(result, max_action="MONITOR"):
    current = str(result.get("vie_action", "NONE")).upper()
    order = {"NONE": 0, "SOFT_FLAG": 1, "MONITOR": 2, "WARN": 3, "BLOCK": 4, "LAW_ENFORCEMENT_REFERRAL": 5}
    if order.get(current, 0) > order.get(max_action, 2):
        result["vie_action"] = max_action
    return result

def _apply_relationship_guardrails(result, relationship_type="stranger"):
    relationship_type = str(relationship_type or "stranger").lower()
    if relationship_type == "stranger":
        return result

    signal_names = _extract_signal_names(result)

    hard_groom = len(signal_names & ROMANCE_HARD_GROOMING_SIGNALS)
    hard_scam = len(signal_names & ROMANCE_SCAM_HARD_SIGNALS)
    low_rapid = len(signal_names & LOW_SEVERITY_RAPID_INTIMACY)
    dampeners = len(signal_names & RECIPROCAL_DAMPENERS)

    no_money = not any(x in signal_names for x in {
        "financial_ask_escalation", "money_request", "resource_extraction", "gift_lure_fee_extraction"
    })
    no_offplatform = not any(x in signal_names for x in {
        "off_platform_migration", "platform_migration_early", "secrecy_encouragement"
    })
    no_coercion = not any(x in signal_names for x in {
        "coercive_sexual_progression", "boundary_conditioning", "isolation_from_others", "threat", "blackmail"
    })

    # Block grooming unless several hard predation conditions are present
    if hard_groom < 3:
        result = _remove_label(result, "grooming")
        result = _remove_label(result, "early stage grooming indicators")
        result = _remove_label(result, "predation")
        if result.get("phase") in {"GROOMING", "COERCION", "ENDGAME"}:
            result["phase"] = "NONE"

    # If there is quick sexual/flirty escalation without hard exploitation, downgrade it
    if low_rapid >= 1 and hard_groom == 0 and hard_scam == 0:
        result = _ensure_label(result, "rapid_flirtation")
        result = _ensure_label(result, "needs_verification")
        result = _cap_risk(result, 24)
        result = _set_action_at_most(result, "MONITOR")
        result = _set_summary(
            result,
            "Conversation shows identity ambiguity and quick flirtation escalation, but no clear financial coercion or exploitative behavior in the visible exchange."
        )

    # Explicit false-positive guardrail
    if no_money and no_offplatform and no_coercion and dampeners >= 1:
        result = _remove_label(result, "grooming")
        result = _remove_label(result, "possible_predation")
        result = _ensure_label(result, "identity_ambiguity")
        result = _ensure_label(result, "monitor_only")
        result = _cap_risk(result, 20)
        result = _set_action_at_most(result, "MONITOR")
        result = _set_summary(
            result,
            "Conversation shows identity ambiguity and quick flirtation escalation, but no clear financial coercion, secrecy pressure, or exploitative behavior in the visible exchange."
        )

    # Narrow romance scam label
    if hard_scam >= 2:
        result = _ensure_label(result, "possible_romance_scam")
    else:
        result = _remove_label(result, "possible_romance_scam")

    # Prefer safer narrow labels
    if "rapid_flirtation" in (result.get("labels", []) or []):
        result = _ensure_label(result, "benign_flirting" if dampeners >= 1 and no_money and no_coercion else "fast_escalation")

    return result

'''

if "_apply_relationship_guardrails" not in text:
    idx = text.find(marker)
    if idx == -1:
        raise SystemExit("Could not find analyze_text() in app/analyzer.py")
    text = text[:idx] + insert_code + "\n" + text[idx:]

old_snippets = [
    'return result',
    '    return result',
]

replaced = False
for s in old_snippets:
    target = s
    if "relationship_type=relationship_type" not in text:
        pass

# Add guardrail call immediately before the final return inside analyze_text
needle = '    return result'
replacement = '''    result = _apply_relationship_guardrails(result, relationship_type=relationship_type)
    return result'''
if needle in text and "_apply_relationship_guardrails(result, relationship_type=relationship_type)" not in text:
    text = text.replace(needle, replacement, 1)
    replaced = True

path.write_text(text, encoding="utf-8")
print("Patched app/analyzer.py")
