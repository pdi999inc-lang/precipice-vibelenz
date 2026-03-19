from pathlib import Path

path = Path("app/main.py")
text = path.read_text(encoding="utf-8")

bad = '        result = _downgrade_false_positive_grooming(result, relationship_type)`r`n        result = _estimate_interest(result, extracted_text)'
good = '''        result = _downgrade_false_positive_grooming(result, relationship_type)
        result = _estimate_interest(result, extracted_text)'''

if bad not in text:
    raise SystemExit("Bad literal newline sequence not found")

text = text.replace(bad, good, 1)
path.write_text(text, encoding="utf-8")
print("Fixed broken analysis lines in app/main.py")
