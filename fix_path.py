content = open("app/main.py", encoding="utf-8").read()
content = content.replace(
    'path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "og-image.svg")',
    'path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "static", "og-image.svg")'
)
open("app/main.py", "w", encoding="utf-8").write(content)
print("Done", len(content))
