from pathlib import Path
import re

path = Path("app/analyzer.py")
text = path.read_text(encoding="utf-8")

helper = r'''
def _extract_first_json_object(raw_text: str):
    import json
    import re

    if raw_text is None:
        raise ValueError("Claude response was empty")

    s = str(raw_text).strip()

    s = re.sub(r"^\s*```(?:json)?\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*```\s*$", "", s)

    try:
        return json.loads(s)
    except Exception:
        pass

    start = s.find("{")
    if start == -1:
        raise ValueError("No JSON object found in Claude response")

    depth = 0
    in_string = False
    escape = False

    for i in range(start, len(s)):
        ch = s[i]

        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = s[start:i + 1]
                    return json.loads(candidate)

    raise ValueError("No complete balanced JSON object found in Claude response")
'''

if "_extract_first_json_object" not in text:
    marker = "def analyze_text("
    idx = text.find(marker)
    if idx == -1:
        raise SystemExit("Could not find analyze_text() in app/analyzer.py")
    text = text[:idx] + helper + "\n\n" + text[idx:]

patterns = [
    (r'json\.loads\(\s*response_text\s*\)', '_extract_first_json_object(response_text)'),
    (r'json\.loads\(\s*raw_text\s*\)', '_extract_first_json_object(raw_text)'),
    (r'json\.loads\(\s*content\s*\)', '_extract_first_json_object(content)'),
    (r'json\.loads\(\s*claude_text\s*\)', '_extract_first_json_object(claude_text)'),
    (r'json\.loads\(\s*response_content\s*\)', '_extract_first_json_object(response_content)'),
    (r'json\.loads\(\s*response_body\s*\)', '_extract_first_json_object(response_body)'),
    (r'json\.loads\(\s*result_text\s*\)', '_extract_first_json_object(result_text)'),
]

changed = 0
for pattern, replacement in patterns:
    text, count = re.subn(pattern, replacement, text)
    changed += count

path.write_text(text, encoding="utf-8")
print(f"Patched app/analyzer.py; replacements made: {changed}")
