"""Prüft, ob jedes Menü, jeder Knopf, jedes Häkchen, Feld und Fenster in der Anleitung vorkommt.

    python tools/doc_audit.py            # fehlende Begriffe der deutschen Anleitung
    python tools/doc_audit.py en         # englische Anleitung (Begriffe über en.json übersetzt)
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUI = os.path.join(ROOT, "tilaser", "gui")
FILES = ("main_window.py", "panels.py", "machine_panel.py", "camera_live.py", "dialogs.py")
KINDS = {"_act": "Menüpunkt", "QPushButton": "Knopf", "QCheckBox": "Häkchen", "QGroupBox": "Gruppe",
         "addTab": "Tab", "addMenu": "Menü", "setWindowTitle": "Fenster", "QRadioButton": "Option",
         "addRow": "Feld", "addItem": "Auswahl"}
# Bezeichnungen, die keine Erklärung brauchen (Zeichen, Platzhalter, Daten)
IGNORE = {"tilaser", "…", "+", "−", "↑", "↓", "✕", "x", "y", "b", "h", "∠", "skal", "keine kamera gefunden",
          "vorlage wählen –", "ctrl+n"}


def norm(s: str) -> str:
    s = s.replace("&", "").replace("…", "").replace("...", "")
    s = re.sub(r"^[^\wÄÖÜäöü(]+", "", s)
    s = re.sub(r"[:\s]+$", "", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def inventory() -> list[tuple[str, str, str]]:
    out = []
    for fn in FILES:
        tree = ast.parse(open(os.path.join(GUI, fn), encoding="utf-8").read())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if name not in KINDS:
                continue
            for a in node.args[:3]:
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    if re.search(r"[A-Za-zÄÖÜäöü]{2}", a.value) and "{" not in a.value:
                        out.append((KINDS[name], a.value, fn))
                    break
    seen, res = set(), []
    for k, t, fn in out:
        n = norm(t)
        if n and n not in IGNORE and n not in seen and len(n) > 1:
            seen.add(n)
            res.append((k, t, fn))
    return res


def missing(lang: str = "de") -> list[tuple[str, str, str]]:
    doc = "Bedienungsanleitung.md" if lang == "de" else f"Manual_{lang}.md"
    text = open(os.path.join(ROOT, "docs", doc), encoding="utf-8").read()
    text = re.sub(r"\s+", " ", text.replace("&", "").replace("*", "").replace("`", "").replace("…", "").replace("\\$", "$")).lower()
    tr = {}
    if lang != "de":
        tr = json.load(open(os.path.join(ROOT, "tilaser", "data", "lang", f"{lang}.json"), encoding="utf-8"))
    out = []
    for k, t, fn in inventory():
        label = tr.get(t, t) if tr else t
        if norm(label) not in text:
            out.append((k, label, fn))
    return out


if __name__ == "__main__":
    lang = sys.argv[1] if len(sys.argv) > 1 else "de"
    m = missing(lang)
    for k, t, fn in m:
        print(f"{k:10} {t!r:60} {fn}")
    print(f"{len(m)} von {len(inventory())} Bezeichnungen fehlen in der Anleitung ({lang})", file=sys.stderr)
