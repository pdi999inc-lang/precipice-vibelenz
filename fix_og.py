content = open("app/main.py", encoding="utf-8").read()
content = content.replace(
    '@app.get("/health")',
    '@app.get("/static/og-image.svg")\nasync def og_image():\n    from fastapi.responses import FileResponse\n    import os\n    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "og-image.svg")\n    return FileResponse(path, media_type="image/svg+xml")\n\n\n@app.get("/health")'
)
open("app/main.py", "w", encoding="utf-8").write(content)
print("Done", len(content))
