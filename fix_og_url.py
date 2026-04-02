for f in ["templates/pitch.html", "templates/index.html"]:
    content = open(f, encoding="utf-8").read()
    content = content.replace(
        "https://appvibelenz.com/static/og-image.svg",
        "https://raw.githubusercontent.com/pdi999inc-lang/precipice-vibelenz/main/static/og-image.svg"
    )
    open(f, "w", encoding="utf-8").write(content)
    print(f, len(content))
