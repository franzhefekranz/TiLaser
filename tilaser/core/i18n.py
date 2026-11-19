"""Übersetzungen der Oberfläche.

Die Texte im Programm sind Deutsch. Eine Sprachdatei ordnet jedem deutschen Text die Übersetzung zu
(JSON, ``"Deutscher Text": "Übersetzung"``). Platzhalter wie ``{0}`` stehen für Zahlen oder Namen, die
erst zur Laufzeit feststehen; sie müssen in der Übersetzung wieder vorkommen (Reihenfolge beliebig).

Sprachdateien:
* mitgeliefert: ``tilaser/data/lang/<code>.json`` (z. B. ``en.json``)
* eigene:       ``<Einstellungsordner>/lang/<code>.json`` – ergänzt bzw. überschreibt die mitgelieferte

Einträge, die mit ``_`` beginnen, sind Angaben zur Datei (``_name`` = Name der Sprache im Menü).
Fehlt eine Übersetzung, wird bei anderen Sprachen als Deutsch die englische verwendet, sonst der
deutsche Text. Fehlende Texte werden gesammelt (``missing``) und beim Beenden in
``lang/fehlend_<code>.json`` geschrieben, damit man sie gezielt nachtragen kann.
"""
from __future__ import annotations

import json
import locale
import os
import re

from . import storage

BUILTIN_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "lang")
USER_DIR = os.path.join(storage.CONFIG_DIR, "lang")
SOURCE = "de"

_PH = re.compile(r"\{(\d+)\}")
_SENT = re.compile(r"(?<=[.!?:])\s+(?=[A-ZÄÖÜ„\"(•⚠🔥])")


def _read(path: str) -> dict:
    try:
        with open(path, encoding="utf-8-sig") as f:
            d = json.load(f)
        return {str(k): str(v) for k, v in d.items() if isinstance(v, str)}
    except (OSError, ValueError):
        return {}


def available() -> dict[str, str]:
    """Sprachcode -> Anzeigename (Deutsch immer dabei)."""
    out = {"de": "Deutsch"}
    for d in (BUILTIN_DIR, USER_DIR):
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            m = re.fullmatch(r"([A-Za-z]{2,3}(?:[_-][A-Za-z]{2})?)\.json", fn)
            if m:
                code = m.group(1)
                name = _read(os.path.join(d, fn)).get("_name") or out.get(code) or code
                out[code] = name
    return out


def system_language() -> str:
    try:
        loc = locale.getlocale()[0] or os.environ.get("LANG", "")
    except ValueError:
        loc = os.environ.get("LANG", "")
    code = (loc or "").split("_")[0].split(".")[0].lower()
    if code in ("de", "german", "deutsch"):
        return "de"
    return code if code in available() else "en"


def norm(s: str) -> str:
    return s.replace("&", "").strip().rstrip("…").rstrip(".").rstrip(":").strip()


class Translator:
    def __init__(self, code: str = SOURCE):
        self.code = code or SOURCE
        self.table: dict[str, str] = {}
        self.missing: dict[str, str] = {}
        if self.code != SOURCE:
            files = []
            if self.code != "en":
                files += [os.path.join(BUILTIN_DIR, "en.json"), os.path.join(USER_DIR, "en.json")]
            files += [os.path.join(BUILTIN_DIR, f"{self.code}.json"), os.path.join(USER_DIR, f"{self.code}.json")]
            for f in files:
                self.table.update({k: v for k, v in _read(f).items() if not k.startswith("_") and v})
        self._norm: dict[str, str] = {}
        self._patterns = []
        for k, v in self.table.items():
            nk = norm(k)
            if nk and nk not in self._norm:
                self._norm[nk] = norm(v)
            if _PH.search(k):
                rx = "^" + _PH.sub(lambda m: f"(?P<p{m.group(1)}>[\\d:.,]+|.+?)", re.escape(k).replace(r"\{", "{")
                                   .replace(r"\}", "}")) + "$"
                try:
                    self._patterns.append((re.compile(rx, re.S), v))
                except re.error:
                    pass
        self._patterns.sort(key=lambda p: -len(p[0].pattern))      # spezifischere zuerst
        self._cache: dict[str, str] = {}

    @property
    def active(self) -> bool:
        return self.code != SOURCE

    def tr(self, s):
        if not self.active or not isinstance(s, str) or not s.strip():
            return s
        c = self._cache.get(s)
        if c is not None:
            return c
        r = self._tr(s, top=True)
        self._cache[s] = r
        return r

    def _lookup(self, s: str):
        v = self.table.get(s)
        if v is not None:
            return v
        st = s.strip()
        if st != s and st in self.table:
            lead = s[: len(s) - len(s.lstrip())]
            trail = s[len(s.rstrip()):]
            return lead + self.table[st] + trail
        n = norm(s)
        if n in self._norm and n == s.strip():
            return self._norm[n]
        for rx, v in self._patterns:
            m = rx.match(s)
            if m:
                out = v
                for k, val in m.groupdict().items():
                    out = out.replace("{" + k[1:] + "}", self.tr(val) if not _numeric(val) else val)
                return out
        return None

    def _tr(self, s: str, top: bool = False) -> str:
        v = self._lookup(s)
        if v is not None:
            return v
        # zusammengesetzte Texte stückweise übersetzen
        for sep in (r"(\n+)", r"(<br\s*/?>)", r"(\s+[–—]\s+)"):
            parts = re.split(sep, s)
            if len(parts) > 1:
                return "".join(p if i % 2 else self._tr(p) for i, p in enumerate(parts))
        parts = _SENT.split(s)
        if len(parts) > 1:
            return " ".join(self._tr(p) for p in parts)
        m = re.match(r"^(.*?\S)(\s*\([^()]*\))$", s, re.S)            # „Öffnen (Strg+O)“
        if m and m.group(1).strip():
            head = self._lookup(m.group(1))
            if head is not None:
                return head + m.group(2)
        m = re.match(r"^([^\w„\"(<]*)(.*?)([^\w)>“\"%]*)$", s, re.S)   # Zeichen wie ▶ ● ⚠ am Rand
        if m and (m.group(1) or m.group(3)) and m.group(2):
            inner = self._lookup(m.group(2))
            if inner is not None:
                return m.group(1) + inner + m.group(3)
        for sep in (", ", " (", "  "):                                  # Text aus zwei bekannten Teilen
            i = s.find(sep, 1)
            while i > 0:
                head, tail = self._lookup(s[:i]), self._lookup(s[i:])
                if head is not None and tail is not None:
                    return head + tail
                i = s.find(sep, i + 1)
        if _german(s):
            self.missing.setdefault(s, "")
        return s

    def save_missing(self):
        if not self.active or not self.missing:
            return None
        os.makedirs(USER_DIR, exist_ok=True)
        path = os.path.join(USER_DIR, f"fehlend_{self.code}.json")
        old = _read(path)
        old.update({k: v for k, v in self.missing.items() if k not in old})
        with open(path, "w", encoding="utf-8") as f:
            json.dump(dict(sorted(old.items())), f, ensure_ascii=False, indent=1)
        return path


def _numeric(s: str) -> bool:
    return bool(re.fullmatch(r"[\d\s.,:+\-−×x%°/]*", s))


def _german(s: str) -> bool:
    return bool(re.search(r"[äöüÄÖÜß]|\b(der|die|das|und|nicht|mit|für|ein|eine|ist|wird|bitte)\b", s))


_current = Translator(SOURCE)


def set_language(code: str) -> Translator:
    global _current
    _current = Translator(code)
    return _current


def current() -> Translator:
    return _current


def tr(s):
    return _current.tr(s)


def make_template(code: str, name: str, keys: list[str], base: str = "en") -> str:
    """Eigene Sprachdatei anlegen: alle Texte, vorbelegt mit der Übersetzung aus `base`."""
    os.makedirs(USER_DIR, exist_ok=True)
    path = os.path.join(USER_DIR, f"{code}.json")
    data = _read(path)
    ref = _read(os.path.join(BUILTIN_DIR, f"{base}.json"))
    out = {"_name": data.get("_name") or name, "_hinweis": "Links der deutsche Text, rechts die Übersetzung. "
           "Platzhalter wie {0} beibehalten. Leere Einträge zeigen Englisch."}
    for k in keys:
        out[k] = data.get(k, ref.get(k, ""))
    for k, v in data.items():
        out.setdefault(k, v)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    return path


def all_keys() -> list[str]:
    return [k for k in _read(os.path.join(BUILTIN_DIR, "en.json")) if not k.startswith("_")]
