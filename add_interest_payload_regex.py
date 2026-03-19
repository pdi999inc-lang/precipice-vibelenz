from pathlib import Path
import re

path = Path("app/main.py")
text = path.read_text(encoding="utf-8")

if '"interest_score": result.get("interest_score")' in text:
    print("Interest payload already present. No change made.")
    raise SystemExit(0)

pattern = r'("positive_signals"\s*:\s*result\.get\("positive_signals",\s*\[\]\),)'

replacement = r'''\1
              "interest_score": result.get("interest_score"),
              "interest_label": result.get("interest_label"),'''

new_text, count = re.subn(pattern, replacement, text, count=1)

if count != 1:
    raise SystemExit("Could not insert interest fields after positive_signals")

path.write_text(new_text, encoding="utf-8")
print("Inserted interest_score and interest_label into TemplateResponse payload")
