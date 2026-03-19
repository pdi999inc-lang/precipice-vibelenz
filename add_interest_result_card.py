from pathlib import Path

path = Path("templates/result.html")
text = path.read_text(encoding="utf-8")

card = '''
    {% if interest_score is not none %}
    <div class="card" style="border-color:#1d4ed8;">
      <h2 style="margin-bottom:0.5rem;">Interest Level</h2>
      <div style="font-size:1.8rem;font-weight:700;color:#93c5fd;">{{ interest_label }}</div>
      <div style="margin-top:0.35rem;color:#cbd5e1;">Score: {{ interest_score }}/100</div>
      <p style="margin-top:0.75rem;color:#94a3b8;font-size:0.92rem;">
        This is an engagement estimate based on reciprocity, tone, question-asking, effort, and visible conversation behavior.
      </p>
    </div>
    {% endif %}
'''

if "Interest Level" in text:
    print("Interest card already present. No change made.")
    raise SystemExit(0)

for marker in ['<div class="card">', '<main>', '<body>']:
    if marker in text:
        text = text.replace(marker, marker + "\n" + card, 1)
        path.write_text(text, encoding="utf-8")
        print("Inserted interest card into templates/result.html")
        break
else:
    raise SystemExit("Could not find insertion point in templates/result.html")
