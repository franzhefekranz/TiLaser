"""Baut die Projektseite (GitHub Pages) nach build/site.

    python tools/make_site.py [ausgabeordner]

- site/index.html mit eingebetteter Kopfgrafik (site/hero.svg) und Screenshot-Galerie
- anleitung.html aus docs/Bedienungsanleitung.md (pandoc), PDF der Anleitung, falls vorhanden
"""
from __future__ import annotations

import html
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = os.path.join(ROOT, "site")
SHOTS = os.path.join(ROOT, "docs", "screenshots")
GALERIE = [("07_auswahl_anfasser", "Skalieren und Drehen mit Anfassern"),
           ("08_knoten", "Knotenbearbeitung mit Kurven-Anfassern"),
           ("10_vorschau", "Werkzeugweg-Vorschau mit Zeitschätzung"),
           ("29_nesting_ergebnis", "Nesting mit Mindestabstand"),
           ("05_ebenen", "Ebenen, Parameter und Material-Presets"),
           ("26_materialtest", "Materialtest Leistung × Geschwindigkeit")]


def gallery(out: str) -> str:
    main = os.path.join(SHOTS, "01_hauptfenster.png")
    if not os.path.exists(main):
        return ""
    os.makedirs(os.path.join(out, "screenshots"), exist_ok=True)
    for fn in os.listdir(SHOTS):
        if fn.endswith(".png"):
            shutil.copy2(os.path.join(SHOTS, fn), os.path.join(out, "screenshots", fn))
        elif os.path.isdir(os.path.join(SHOTS, fn)):          # übersetzte Aufnahmen (screenshots/en)
            shutil.copytree(os.path.join(SHOTS, fn), os.path.join(out, "screenshots", fn), dirs_exist_ok=True)
    items = []
    for name, cap in GALERIE:
        if os.path.exists(os.path.join(SHOTS, name + ".png")):
            items.append(f'<li><a href="screenshots/{name}.png"><figure><img src="screenshots/{name}.png" '
                         f'alt="{html.escape(cap)}" loading="lazy"><figcaption>{html.escape(cap)}</figcaption>'
                         f'</figure></a></li>')
    return ('<section class="galerie" aria-labelledby="galerie-titel">\n'
            '  <h2 id="galerie-titel">So sieht TiLaser aus</h2>\n'
            '  <figure class="gross"><img src="screenshots/01_hauptfenster.png" alt="Hauptfenster von TiLaser mit '
            'Arbeitsfläche, Ebenen und Auftragsleiste"><figcaption>Das Hauptfenster: Arbeitsfläche, Zeichenwerkzeuge, '
            'Ebenen und die Auftragsleiste mit Start, Pause und Stopp.</figcaption></figure>\n'
            f'  <ul class="reihe">{"".join(items)}</ul>\n</section>')


MANUALS = {
    "de": {"src": "Bedienungsanleitung.md", "html": "anleitung.html", "pdf": "TiLaser-Bedienungsanleitung.pdf",
           "title": "TiLaser – Bedienungsanleitung", "home": "TiLaser-Startseite", "toc": "Inhalt",
           "dl": "Anleitung als PDF herunterladen", "ph": "Screenshot „{}“ folgt"},
    "en": {"src": "Manual_en.md", "html": "manual_en.html", "pdf": "TiLaser-Manual.pdf",
           "title": "TiLaser – User Manual", "home": "TiLaser home page", "toc": "Contents",
           "dl": "Download the manual as PDF", "ph": "Screenshot “{}” will follow"},
}


def manual_html(out: str, lang: str = "de"):
    M = MANUALS[lang]
    if not shutil.which("pandoc"):
        print(f"pandoc fehlt – {M['html']} wird übersprungen")
        return
    with open(os.path.join(ROOT, "docs", M["src"]), encoding="utf-8") as f:
        md = f.read()
    md = re.sub(r"(?ms)^## (Inhalt|Contents)\n.*?(?=^---\n)", "", md)
    md = re.sub(r"(?ms)^> (Die Screenshots werden|The screenshots are).*?\n\n", "", md)
    md = re.sub(r"(?m)^---\s*$", "", md)                      # Kapitel haben bereits eine Linie

    def img(m):
        alt, path = m.group(1), m.group(2)
        fn = os.path.basename(path)
        if fn.startswith("icon_") and os.path.exists(os.path.join(SHOTS, fn)):
            return f"![{alt}](screenshots/{fn}){{.symbol}}"
        rel = path[len("screenshots/"):] if path.startswith("screenshots/") else fn
        if os.path.exists(os.path.join(SHOTS, rel)):          # übersetzte Aufnahme, z. B. screenshots/en/…
            return f"![{alt}](screenshots/{rel})"
        if os.path.exists(os.path.join(SHOTS, fn)):
            return f"![{alt}](screenshots/{fn})"
        return f'<div class="platzhalter">{html.escape(M["ph"].format(alt))}</div>'

    md = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", img, md)
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "a.md")
        tpl = os.path.join(td, "t.html")
        with open(src, "w", encoding="utf-8") as f:
            f.write(md)
        with open(tpl, "w", encoding="utf-8") as f:
            f.write("""<!DOCTYPE html>
<html lang="@LANG@"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>@TITLE@</title><link rel="icon" type="image/png" href="tilaser.png">
<link rel="stylesheet" href="anleitung.css"></head>
<body><nav id="TOC" aria-label="@TOCNAME@"><a class="zurueck" href="./">@HOME@</a>$toc$</nav>
<main class="inhalt">$body$
<p><a href="@PDF@">@DL@</a></p></main></body></html>
""".replace("@LANG@", lang).replace("@TITLE@", M["title"]).replace("@HOME@", M["home"])
                    .replace("@TOCNAME@", M["toc"]).replace("@PDF@", M["pdf"]).replace("@DL@", M["dl"]))
        subprocess.run(["pandoc", src, "-f", "markdown+gfm_auto_identifiers+pipe_tables", "-t", "html5",
                        "-M", f"title={M['title']}",
                        "--template", tpl, "--toc", "--toc-depth=2", "-o", os.path.join(out, M["html"])],
                       check=True)


def main(out: str):
    if os.path.exists(out):
        shutil.rmtree(out)
    os.makedirs(out)
    with open(os.path.join(SITE, "hero.svg"), encoding="utf-8") as f:
        hero = f.read()
    with open(os.path.join(SITE, "index.html"), encoding="utf-8") as f:
        page = f.read()
    page = page.replace("{{HERO_SVG}}", hero).replace("{{GALLERY}}", gallery(out))
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)
    for fn in ("style.css", "script.js", "anleitung.css"):
        shutil.copy2(os.path.join(SITE, fn), os.path.join(out, fn))
    shutil.copy2(os.path.join(ROOT, "packaging", "icon", "tilaser-256.png"), os.path.join(out, "tilaser.png"))
    for lang, M in MANUALS.items():
        pdf = os.path.join(ROOT, "docs", M["pdf"])
        if os.path.exists(pdf):
            shutil.copy2(pdf, os.path.join(out, M["pdf"]))
        manual_html(out, lang)
    open(os.path.join(out, ".nojekyll"), "w").close()
    print(f"Projektseite erzeugt in {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "build", "site"))
