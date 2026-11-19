"""Bedienungsanleitung für die Hilfe im Programm finden und aufbereiten (ohne Qt, getestet)."""
from __future__ import annotations

import os
import re
import sys

ONLINE = {"de": "https://franzhefekranz.github.io/TiLaser/anleitung.html",
          "en": "https://franzhefekranz.github.io/TiLaser/manual_en.html"}
FILES = {"de": ("Bedienungsanleitung.md", "TiLaser-Bedienungsanleitung.pdf"),
         "en": ("Manual_en.md", "TiLaser-Manual.pdf")}


def docs_dir() -> str | None:
    here = os.path.dirname(os.path.abspath(__file__))          # tilaser/core
    cands = []
    if getattr(sys, "_MEIPASS", None):
        cands.append(os.path.join(sys._MEIPASS, "docs"))
    if getattr(sys, "frozen", False):
        exe = os.path.dirname(sys.executable)
        cands += [os.path.join(exe, "_internal", "docs"), os.path.join(exe, "docs")]
    cands += [os.path.join(os.path.dirname(os.path.dirname(here)), "docs"), "/usr/share/doc/tilaser"]
    for c in cands:
        if os.path.exists(os.path.join(c, "Bedienungsanleitung.md")):
            return c
    return None


def slug(title: str) -> str:
    """Anker wie bei GitHub/pandoc (gfm_auto_identifiers)."""
    t = re.sub(r"[*`]", "", title).strip().lower()
    t = "".join(ch for ch in t if ch.isalnum() or ch in " -_")
    return t.replace(" ", "-")


def manual_files(lang: str):
    return FILES.get(lang, FILES["en"]) if lang != "de" else FILES["de"]


def prepare(md: str, base: str | None = None):
    """Markdown für die Anzeige vorbereiten. Liefert (Text, Überschriften) mit
    Überschriften = Liste (Ebene, Titel, Anker, Hilfe-Kennung oder None)."""
    out, heads = [], []
    pending, fence = None, False
    for line in md.split("\n"):
        if line.startswith("```"):
            fence = not fence
        m = re.match(r"^<!--\s*hilfe:\s*([\w-]+)\s*-->\s*$", line)
        if m and not fence:
            pending = m.group(1)
            continue
        if not fence and re.match(r"^<!--.*-->\s*$", line):
            continue
        h = re.match(r"^(#{1,6})\s+(.*?)\s*$", line) if not fence else None
        if h:
            title = re.sub(r"[*`]", "", h.group(2))
            heads.append((len(h.group(1)), title, slug(title), pending))
            pending = None
        if base and not fence:                         # fehlende übersetzte Aufnahme -> deutsche verwenden
            def img(mm):
                p = mm.group(2)
                if not os.path.exists(os.path.join(base, p)):
                    alt = os.path.join("screenshots", os.path.basename(p))
                    if os.path.exists(os.path.join(base, alt)):
                        p = alt
                return f"![{mm.group(1)}]({p})"
            line = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", img, line)
        out.append(line)
    return "\n".join(out), heads


def help_ids(md: str) -> set[str]:
    return set(re.findall(r"^<!--\s*hilfe:\s*([\w-]+)\s*-->", md, re.M))
