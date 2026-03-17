from pathlib import Path
import re

path = Path("app/analyzer.py")
text = path.read_text(encoding="utf-8")

pattern = r'def _set_summary\(result, text\):\r?\n(?:    .*\r?\n)+?def _ensure_label'
replacement = '''def _set_summary(result, text):
    result["summary"] = text
    return result

def _ensure_label'''

new_text, count = re.subn(pattern, replacement, text, count=1)

if count != 1:
    raise SystemExit("Could not safely replace _set_summary block")

path.write_text(new_text, encoding="utf-8")
print("Fixed _set_summary in app/analyzer.py")
