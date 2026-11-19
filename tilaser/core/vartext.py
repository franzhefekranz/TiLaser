"""Variabler Text und Barcodes.

Platzhalter im Text (auch im Inhalt von QR-Code und Barcode):
  {nr}            fortlaufende Nummer (Startwert/Schrittweite unter Werkzeuge → Variable Texte)
  {nr:04}         mit führenden Nullen auf 4 Stellen
  {datum}         Tagesdatum 18.11.2026      {zeit}  14:05      {jahr} {monat} {tag}
  {spalte}        Wert der Spalte „spalte“ aus der aktuellen CSV-Zeile (z. B. {Name})
Objekte mit Platzhaltern werden vor jedem Auftrag/Export neu erzeugt; nach dem Auftrag wird die
Nummer weitergezählt bzw. die nächste CSV-Zeile genommen.
"""
from __future__ import annotations

import csv
import datetime as _dt
import re
from dataclasses import asdict, dataclass

import numpy as np

from . import geometry as g
from .barcode import code128_widths, qr_matrix
from .model import Item, Project, VectorPath, new_id

_PH = re.compile(r"\{([^{}:]+)(?::([^{}]*))?\}")


@dataclass
class VarState:
    nr: int = 1
    step: int = 1
    csv_path: str = ""
    csv_row: int = 0             # 0-basiert, ohne Kopfzeile
    auto_advance: bool = True

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(**{k: v for k, v in (d or {}).items() if k in cls.__dataclass_fields__})


def read_csv(path: str) -> tuple[list[str], list[dict]]:
    """CSV mit Kopfzeile lesen (Trennzeichen ; oder , automatisch)."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
        except csv.Error:
            dialect = csv.excel
        rows = list(csv.DictReader(f, dialect=dialect))
    head = list(rows[0].keys()) if rows else []
    return head, rows


def render(template: str, st: VarState, now: _dt.datetime | None = None, row: dict | None = None) -> str:
    now = now or _dt.datetime.now()
    row = row or {}
    lower = {k.strip().lower(): v for k, v in row.items() if k}

    def sub(m):
        key, spec = m.group(1).strip(), m.group(2)
        k = key.lower()
        if k == "nr":
            return format(st.nr, spec) if spec else str(st.nr)
        if k == "datum":
            return now.strftime(spec or "%d.%m.%Y")
        if k == "zeit":
            return now.strftime(spec or "%H:%M")
        if k == "jahr":
            return f"{now.year}"
        if k == "monat":
            return f"{now.month:02d}"
        if k == "tag":
            return f"{now.day:02d}"
        if k in lower:
            return str(lower[k] or "")
        return m.group(0)                      # unbekannt: stehen lassen
    return _PH.sub(sub, template)


def has_placeholders(template: str) -> bool:
    return bool(_PH.search(template or ""))


# --------------------------------------------------------------------------- Geometrie
def _rect(x, y, w, h):
    return g.Polyline(np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]], float), True)


def qr_polys(text: str, size_mm: float, ecl: str = "M", quiet: int = 0) -> list[g.Polyline]:
    """QR-Code als Rechtecke (je Zeile zusammengefasste dunkle Module), zentriert um (0, 0)."""
    m = qr_matrix(text, ecl)
    n = len(m)
    mod = size_mm / (n + 2 * quiet)
    half = size_mm / 2
    out = []
    for r, row in enumerate(m):
        c = 0
        while c < n:
            if row[c]:
                c0 = c
                while c < n and row[c]:
                    c += 1
                x = -half + (quiet + c0) * mod
                y = half - (quiet + r + 1) * mod
                out.append(_rect(x, y, (c - c0) * mod, mod))
            else:
                c += 1
    return _merge_vertical(out)


def _merge_vertical(rects: list[g.Polyline]) -> list[g.Polyline]:
    """Gleich breite, übereinanderliegende Rechtecke zusammenfassen (weniger Konturen)."""
    key: dict[tuple[float, float], list] = {}
    for p in rects:
        x0, y0, x1, y1 = g.bbox_of([p])
        key.setdefault((round(x0, 6), round(x1, 6)), []).append([y0, y1])
    out = []
    for (x0, x1), spans in key.items():
        spans.sort()
        cur = spans[0][:]
        for s in spans[1:]:
            if abs(s[0] - cur[1]) < 1e-6:
                cur[1] = s[1]
            else:
                out.append(_rect(x0, cur[0], x1 - x0, cur[1] - cur[0]))
                cur = s[:]
        out.append(_rect(x0, cur[0], x1 - x0, cur[1] - cur[0]))
    return out


def code128_polys(text: str, width_mm: float, height_mm: float) -> list[g.Polyline]:
    w = code128_widths(text)
    mod = width_mm / sum(w)
    x = -width_mm / 2
    out = []
    for i, k in enumerate(w):
        if i % 2 == 0:
            out.append(_rect(x, -height_mm / 2, k * mod, height_mm))
        x += k * mod
    return out


def build_polys(spec: dict, text: str) -> list[g.Polyline]:
    kind = spec.get("kind", "text")
    if kind == "qr":
        return qr_polys(text, float(spec.get("size", 20.0)), spec.get("ecl", "M"))
    if kind == "code128":
        return code128_polys(text, float(spec.get("width", 40.0)), float(spec.get("height", 12.0)))
    from .text_import import text_to_polylines
    polys = text_to_polylines(text, spec["font"], float(spec.get("size", 10.0)), float(spec.get("spacing", 1.2)),
                              float(spec.get("letter", 0.0)), spec.get("align", "left"))
    if polys:
        bb = g.bbox_of(polys)
        polys = [p.transformed(g.translate(-(bb[0] + bb[2]) / 2, -(bb[1] + bb[3]) / 2)) for p in polys]
    return polys


def make_item(spec: dict, layer_id: str, x: float, y: float, st: VarState, row: dict | None = None) -> Item:
    """Neues Objekt (Text, QR-Code oder Barcode) aus spec = {kind, template, …}."""
    it = Item(id=new_id("I"), name="", kind="vector", x=x, y=y)
    it.var = dict(spec, layer=layer_id)
    refresh_item(it, st, row=row)
    return it


def refresh_item(it: Item, st: VarState, now=None, row: dict | None = None) -> str:
    spec = getattr(it, "var", None)
    if not spec:
        return ""
    text = render(spec.get("template", ""), st, now, row)
    polys = build_polys(spec, text) if text.strip() else []
    layer_id = spec.get("layer") or (it.paths[0].layer_id if it.paths else "")
    it.paths = [VectorPath(p, layer_id) for p in polys]
    label = {"qr": "QR", "code128": "Barcode"}.get(spec.get("kind"), "Text")
    it.name = f"{label}: {text.splitlines()[0][:24] if text else ''}"
    return text


def current_row(st: VarState) -> dict | None:
    if not st.csv_path:
        return None
    try:
        _, rows = read_csv(st.csv_path)
    except OSError:
        return None
    return rows[st.csv_row] if 0 <= st.csv_row < len(rows) else None


def refresh_project(project: Project, now=None) -> int:
    """Alle Objekte mit Platzhaltern neu erzeugen. Rückgabe: Anzahl variabler Objekte."""
    st = project_state(project)
    row = current_row(st)
    n = 0
    for it in project.items:
        spec = getattr(it, "var", None)
        if spec and has_placeholders(spec.get("template", "")):
            refresh_item(it, st, now, row)
            n += 1
    return n


def advance(project: Project) -> None:
    st = project_state(project)
    st.nr += st.step
    if st.csv_path:
        st.csv_row += 1


def project_state(project: Project) -> VarState:
    st = getattr(project, "vars", None)
    if not isinstance(st, VarState):
        st = VarState.from_dict(st if isinstance(st, dict) else None)
        project.vars = st
    return st
