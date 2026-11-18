"""Sammelt alle deutschen Oberflächentexte für die Sprachdateien.

    python tools/i18n_extract.py                     # Liste der Texte ausgeben (Anzahl, fehlende in en.json)
    python tools/i18n_extract.py --update            # fehlende Texte leer in tilaser/data/lang/*.json eintragen
    python tools/i18n_extract.py --missing en        # nur die fehlenden Texte für eine Sprache zeigen

Quellen: Zeichenketten im Quelltext der Oberfläche (tilaser/gui) und Meldungen aus tilaser/core
(ohne Docstrings), f-Strings als Muster mit {0}, {1} …, dazu Preset- und Maschinennamen und die
Texte der Weboberfläche.
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
LANG_DIR = os.path.join(ROOT, "tilaser", "data", "lang")

SKIP_CORE = {"i18n.py", "webserver.py"}


def _letters(s):
    return re.sub(r"\{\d+\}", "", s)


def is_ui_text(s: str, core: bool) -> bool:
    t = _letters(s).strip()
    if len(t) < 2 or not re.search(r"[A-Za-zÄÖÜäöüß]{2,}", t):
        return False
    if re.fullmatch(r"[a-z0-9_.\-/:]+", t) or re.fullmatch(r"[A-Z0-9_]+", t):
        return False                                      # Bezeichner, Schlüssel, Konstanten
    if any(x in s for x in ("<svg", "<path", "stroke=", "\\d", "(?", "%(", "{:")):
        return False
    if re.fullmatch(r"[#\w]*#[0-9a-fA-F]{3,8}", t) or re.fullmatch(r"\*?\.\w+( \*?\.\w+)*", t):
        return False
    if re.match(r"^[GM]\d", t) or t.startswith("$") or re.fullmatch(r"[A-Z][A-Z0-9 ,.=\-+]*", t):
        return False                                      # G-Code, GRBL-Befehle
    if core:                                              # im Kern nur echte Sätze/Meldungen
        return bool(re.search(r"[äöüÄÖÜß]", t) or " " in t) and bool(re.search(r"[A-ZÄÖÜ]", t[:3] + t))
    return True


def strings_from(path: str, core: bool = False):
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    skip = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)) and node.body:
            b = node.body[0]
            if isinstance(b, ast.Expr) and isinstance(b.value, ast.Constant) and isinstance(b.value.value, str):
                skip.add(id(b.value))
        if isinstance(node, ast.Subscript):           # d["schluessel"]
            for c in ast.walk(node.slice):
                skip.add(id(c))
        if isinstance(node, ast.Call):                 # getattr(x, "name"), os.path.join(...), Logging usw.
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name in ("getattr", "setattr", "hasattr", "join", "endswith", "startswith", "get", "_set",
                        "setdefault", "pop", "findData", "icon", "_new_path", "split", "replace", "strip",
                        "compile", "match", "sub", "search", "fullmatch", "encode", "decode", "strftime",
                        "send_header", "_send"):
                for a in node.args:
                    if isinstance(a, ast.Constant):
                        skip.add(id(a))
    out, tables = [], []
    if core:            # Tabellen auf Modulebene (Farbnamen, Modi, Musternamen …) zählen als Oberflächentext
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, (ast.Dict, ast.List, ast.Tuple)):
                vals = node.value.values if isinstance(node.value, ast.Dict) else node.value.elts
                for v in vals:
                    for c in ast.walk(v):
                        if isinstance(c, ast.Constant) and isinstance(c.value, str) and \
                                re.fullmatch(r"[A-ZÄÖÜ][\wäöüß .,/()%~–-]*", c.value) and not re.fullmatch(r"[A-Z0-9_]+", c.value):
                            tables.append(c.value)
                            skip.add(id(c))
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            parts, n = [], 0
            for v in node.values:
                if isinstance(v, ast.Constant):
                    parts.append(str(v.value))
                else:
                    parts.append("{%d}" % n)
                    n += 1
                for c in ast.walk(v):
                    skip.add(id(c))
            out.append("".join(parts))
            for v in node.values:
                skip.add(id(v))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            out.append(node.value)
    return tables + [s for s in out if is_ui_text(s, core)]


def collect() -> list[str]:
    keys: dict[str, None] = {}
    gui = os.path.join(ROOT, "tilaser", "gui")
    for fn in sorted(os.listdir(gui)):
        if fn.endswith(".py") and fn not in ("i18n_qt.py", "icons.py"):
            for s in strings_from(os.path.join(gui, fn)):
                keys.setdefault(s)
    tree = ast.parse(open(os.path.join(gui, "icons.py"), encoding="utf-8").read())
    for node in ast.walk(tree):                   # Schlüssel von BY_TEXT (Aktionsnamen)
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "BY_TEXT" for t in node.targets):
            for k in node.value.keys:
                if isinstance(k, ast.Constant):
                    keys.setdefault(k.value)
    core = os.path.join(ROOT, "tilaser", "core")
    for fn in sorted(os.listdir(core)):
        if fn.endswith(".py") and fn not in SKIP_CORE:
            for s in strings_from(os.path.join(core, fn), core=True):
                keys.setdefault(s)
    from tilaser.core.webserver import WEB_PHRASES
    for s in WEB_PHRASES:
        keys.setdefault(s.strip())
    data = os.path.join(ROOT, "tilaser", "data")
    for p in json.load(open(os.path.join(data, "presets.json"), encoding="utf-8")):
        for k in ("name", "material", "machine"):
            if p.get(k):
                keys.setdefault(p[k])
    for m in json.load(open(os.path.join(data, "machines.json"), encoding="utf-8")):
        for k in ("name", "hinweis"):
            if m.get(k):
                keys.setdefault(m[k])
    return list(keys)


def main(argv):
    keys = collect()
    if "--update" in argv:
        for fn in sorted(os.listdir(LANG_DIR)):
            if not fn.endswith(".json"):
                continue
            path = os.path.join(LANG_DIR, fn)
            data = json.load(open(path, encoding="utf-8"))
            added = 0
            for k in keys:
                if k not in data:
                    data[k] = ""
                    added += 1
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
                f.write("\n")
            print(f"{fn}: {added} neue Texte eingetragen")
        return
    if "--missing" in argv:
        code = argv[argv.index("--missing") + 1]
        data = json.load(open(os.path.join(LANG_DIR, f"{code}.json"), encoding="utf-8"))
        miss = [k for k in keys if not data.get(k)]
        for k in miss:
            print(json.dumps(k, ensure_ascii=False))
        print(f"{len(miss)} von {len(keys)} fehlen", file=sys.stderr)
        return
    for k in keys:
        print(json.dumps(k, ensure_ascii=False))
    print(f"{len(keys)} Texte", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1:])
