"""Erzeugt GitHub-Wiki-Seiten aus docs/Bedienungsanleitung.md.

    python tools/make_wiki.py <wiki-ordner>

- Home.md: Einleitung und Inhaltsverzeichnis
- eine Seite je Kapitel (## N. Titel), _Sidebar.md mit Navigation, _Footer.md
- Screenshots werden nach <wiki-ordner>/images kopiert; fehlende Bilder werden durch einen Hinweis ersetzt
- Verweise innerhalb der Anleitung (#anker) werden auf die richtige Wiki-Seite umgeschrieben
"""
from __future__ import annotations

import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "docs", "Bedienungsanleitung.md")
SHOTS = os.path.join(ROOT, "docs", "screenshots")


def slug(title: str) -> str:
    """GitHub-Anker: klein, Leerzeichen -> '-', Satzzeichen entfernen (Umlaute bleiben)."""
    s = title.strip().lower()
    s = re.sub(r"[^\w\- ]", "", s, flags=re.UNICODE)
    return s.replace(" ", "-")


def page_name(title: str) -> str:
    """Wiki-Seitenname aus einem Kapiteltitel, z. B. '9. Ebenen und Laserparameter' -> '09-Ebenen-und-Laserparameter'."""
    m = re.match(r"(\d+)\.\s*(.*)", title)
    num, rest = (int(m.group(1)), m.group(2)) if m else (0, title)
    rest = re.sub(r"[^\w\- ]", "", rest.replace("–", "-").replace(":", ""), flags=re.UNICODE)
    rest = re.sub(r"\s+", "-", rest.strip())
    rest = re.sub(r"-+", "-", rest)
    return f"{num:02d}-{rest}"


def split_chapters(text: str):
    parts = re.split(r"(?m)^## ", text)
    intro = parts[0]
    chapters = []
    for p in parts[1:]:
        title, _, body = p.partition("\n")
        chapters.append((title.strip(), body))
    return intro, chapters


def main(out_dir: str):
    with open(SRC, encoding="utf-8") as f:
        text = f.read()
    intro, chapters = split_chapters(text)
    chapters = [(t, b) for t, b in chapters if t != "Inhalt"]

    # Anker -> Seite
    anchor_page: dict[str, str] = {}
    for title, body in chapters:
        name = page_name(title)
        anchor_page[slug(title)] = (name, "")
        for h in re.findall(r"(?m)^#{3,4}\s+(.+)$", body):
            anchor_page[slug(h)] = (name, slug(h))

    os.makedirs(os.path.join(out_dir, "images"), exist_ok=True)
    missing = set()

    def fix(md: str) -> str:
        def img(m):
            alt, path = m.group(1), m.group(2)
            fn = os.path.basename(path)
            src = os.path.join(SHOTS, fn)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(out_dir, "images", fn))
                return f"![{alt}](images/{fn})"
            missing.add(fn)
            return f"> 🖼 *Screenshot „{alt}“ folgt – er wird automatisch aus dem aktuellen Programm erzeugt.*"
        md = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", img, md)

        def link(m):
            label, anchor = m.group(1), m.group(2)
            if anchor in anchor_page:
                page, sub = anchor_page[anchor]
                return f"[{label}]({page}{'#' + sub if sub else ''})"
            return m.group(0)
        return re.sub(r"\[([^\]]+)\]\(#([^)]+)\)", link, md)

    # Kapitel-Seiten
    names = [page_name(t) for t, _ in chapters]
    for i, (title, body) in enumerate(chapters):
        nav = []
        if i > 0:
            nav.append(f"← [{chapters[i - 1][0]}]({names[i - 1]})")
        nav.append("[Übersicht](Home)")
        if i + 1 < len(chapters):
            nav.append(f"[{chapters[i + 1][0]}]({names[i + 1]}) →")
        content = f"# {title}\n" + fix(body).rstrip().rstrip("-").rstrip() + "\n\n---\n" + " · ".join(nav) + "\n"
        with open(os.path.join(out_dir, names[i] + ".md"), "w", encoding="utf-8") as f:
            f.write(content)

    # Startseite
    intro_body = re.sub(r"(?m)^# .*\n", "", intro, count=1).strip().rstrip("-").strip()
    home = ["# TiLaser – Bedienungsanleitung", "", fix(intro_body), "", "## Kapitel", ""]
    for (title, _), name in zip(chapters, names):
        home.append(f"- [{title}]({name})")
    home += ["", "Die Anleitung gibt es auch als "
             "[PDF](https://github.com/franzhefekranz/TiLaser/raw/main/docs/TiLaser-Bedienungsanleitung.pdf) "
             "und im Repository unter "
             "[docs/Bedienungsanleitung.md](https://github.com/franzhefekranz/TiLaser/blob/main/docs/Bedienungsanleitung.md)."]
    with open(os.path.join(out_dir, "Home.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(home) + "\n")

    short = [re.sub(r"^\d+\.\s*", "", t) for t, _ in chapters]
    side = ["**[TiLaser](Home)**", ""] + [f"{i + 1}. [{st}]({n})" for i, (st, n) in enumerate(zip(short, names))]
    with open(os.path.join(out_dir, "_Sidebar.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(side) + "\n")
    with open(os.path.join(out_dir, "_Footer.md"), "w", encoding="utf-8") as f:
        f.write("Automatisch erzeugt aus "
                "[docs/Bedienungsanleitung.md](https://github.com/franzhefekranz/TiLaser/blob/main/docs/"
                "Bedienungsanleitung.md) – Änderungen bitte dort vornehmen.\n")

    # veraltete Kapitelseiten entfernen
    keep = set(names) | {"Home", "_Sidebar", "_Footer"}
    for fn in os.listdir(out_dir):
        if fn.endswith(".md") and re.match(r"\d\d-", fn) and fn[:-3] not in keep:
            os.remove(os.path.join(out_dir, fn))
    print(f"{len(chapters)} Kapitelseiten erzeugt in {out_dir}"
          + (f", {len(missing)} Screenshots fehlen noch" if missing else ""))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "build", "wiki"))
