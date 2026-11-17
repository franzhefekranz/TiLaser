"""Text -> Konturen über TrueType/OpenType-Schriften (fontTools)."""
from __future__ import annotations

import glob
import math
import os
import sys

import numpy as np

from . import geometry as g


def system_fonts() -> list[str]:
    """Sucht installierte TTF/OTF-Schriften (Windows, Linux, macOS)."""
    dirs = []
    if sys.platform.startswith("win"):
        dirs += [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Microsoft\Windows\Fonts")]
    elif sys.platform == "darwin":
        dirs += ["/System/Library/Fonts", "/Library/Fonts", os.path.expanduser("~/Library/Fonts")]
    else:
        dirs += ["/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.fonts"),
                 os.path.expanduser("~/.local/share/fonts")]
    found = []
    for d in dirs:
        if d and os.path.isdir(d):
            for ext in ("ttf", "otf", "TTF", "OTF"):
                found += glob.glob(os.path.join(d, "**", f"*.{ext}"), recursive=True)
    return sorted(set(found), key=lambda p: os.path.basename(p).lower())


def default_font() -> str | None:
    prefs = ["arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf", "Helvetica.ttc", "Arial.ttf"]
    fonts = system_fonts()
    by_name = {os.path.basename(f).lower(): f for f in fonts}
    for p in prefs:
        if p.lower() in by_name:
            return by_name[p.lower()]
    return fonts[0] if fonts else None


def text_to_polylines(text: str, font_path: str, size_mm: float = 10.0, line_spacing: float = 1.2,
                      letter_spacing_mm: float = 0.0, align: str = "left", tol: float = g.CURVE_TOL
                      ) -> list[g.Polyline]:
    """Erzeugt geschlossene Konturen. size_mm = Versalhöhe-unabhängige Em-Größe."""
    from fontTools.pens.basePen import BasePen
    from fontTools.ttLib import TTFont

    font = TTFont(font_path, fontNumber=0, lazy=True)
    gs = font.getGlyphSet()
    cmap = font.getBestCmap() or {}
    upm = font["head"].unitsPerEm
    s = size_mm / upm
    hmtx = font["hmtx"]
    utol = tol / s

    class Pen(BasePen):
        """Sammelt Glyphenkonturen als Bézier-Pfade (Schrift-Einheiten)."""

        def __init__(self, glyphset):
            super().__init__(glyphset)
            self.bb = g.BezBuilder(utol)

        @property
        def polys(self):
            return [p for p in self.bb.out if p.closed]

        def _moveTo(self, p):
            self.bb.move(p)

        def _lineTo(self, p):
            self.bb.line(p)

        def _curveToOne(self, p1, p2, p3):
            self.bb.cubic(p1, p2, p3)

        def _qCurveToOne(self, p1, p2):
            self.bb.quad(p1, p2)

        def _closePath(self):
            cur = self.bb.current
            if cur is not None and self.bb.a and not np.allclose(self.bb.a[0], cur):
                self.bb.line(self.bb.a[0])
            self.bb.finish(True)

        def _endPath(self):
            self.bb.finish(True)

        def _flush(self, closed=True):
            if self.bb.a:
                self._closePath()

    lines = text.split("\n")
    out: list[g.Polyline] = []
    line_widths = []
    line_polys = []
    for line in lines:
        x = 0.0
        polys_line = []
        for ch in line:
            gname = cmap.get(ord(ch))
            if gname is None:
                gname = ".notdef" if ".notdef" in gs else None
            if gname is None:
                continue
            pen = Pen(gs)
            gs[gname].draw(pen)
            pen._flush(True)
            m = g.translate(x, 0) @ g.scale(s)
            for p in pen.polys:
                polys_line.append(p.transformed(m))
            x += hmtx[gname][0] * s + letter_spacing_mm
        line_widths.append(x - letter_spacing_mm if line else 0)
        line_polys.append(polys_line)
    maxw = max(line_widths) if line_widths else 0
    for i, (polys_line, w) in enumerate(zip(line_polys, line_widths)):
        dx = 0.0
        if align == "center":
            dx = (maxw - w) / 2
        elif align == "right":
            dx = maxw - w
        dy = -i * size_mm * line_spacing
        out += [p.transformed(g.translate(dx, dy)) for p in polys_line]
    font.close()
    return out


def _glyph_runs(text: str, font_path: str, size_mm: float, letter_spacing_mm: float = 0.0,
                tol: float = g.CURVE_TOL):
    """[(Konturen der Glyphe bei x = 0, Vorschub), ...] für eine Zeile."""
    polys_all = text_to_polylines  # nur für Lesbarkeit; eigentliche Arbeit unten
    out = []
    for ch in text:
        if ch == "\n":
            continue
        polys = polys_all(ch, font_path, size_mm, tol=tol) if not ch.isspace() else []
        adv = _advance(ch, font_path, size_mm) + letter_spacing_mm
        out.append((polys, adv))
    return out


_ADV_CACHE: dict = {}


def _advance(ch: str, font_path: str, size_mm: float) -> float:
    key = (font_path,)
    if key not in _ADV_CACHE:
        from fontTools.ttLib import TTFont
        f = TTFont(font_path, fontNumber=0, lazy=True)
        _ADV_CACHE[key] = (f.getBestCmap() or {}, f["hmtx"], f["head"].unitsPerEm)
    cmap, hmtx, upm = _ADV_CACHE[key]
    gname = cmap.get(ord(ch))
    if gname is None:
        return size_mm * 0.5
    return hmtx[gname][0] * size_mm / upm


def text_on_path(text: str, font_path: str, size_mm: float, path_pts: np.ndarray, closed: bool = False,
                 start_offset: float = 0.0, distance: float = 0.0, align: str = "left",
                 letter_spacing_mm: float = 0.0, flip: bool = False) -> list[g.Polyline]:
    """Setzt Text entlang eines Pfades (Weltkoordinaten).

    Offene Pfade: Text läuft in Pfadrichtung, Buchstaben stehen links der Laufrichtung.
    Geschlossene Pfade: Text steht außen und ist oben mittig lesbar (Ausrichtung bezieht sich
    auf den höchsten Punkt der Kontur). flip = innen bzw. Richtung umkehren.
    distance = Abstand der Grundlinie vom Pfad (positiv = vom Pfad weg in Leserichtung „oben“).
    """
    pts = np.asarray(path_pts, float)
    if closed and not np.allclose(pts[0], pts[-1]):
        pts = np.vstack([pts, pts[:1]])
    if closed:
        area = 0.5 * np.sum(pts[:-1, 0] * pts[1:, 1] - pts[1:, 0] * pts[:-1, 1])
        if (area > 0) != flip:                    # außen lesbar = im Uhrzeigersinn laufen
            pts = pts[::-1]
    elif flip:
        pts = pts[::-1]
    seg = np.hypot(*np.diff(pts, axis=0).T)
    cum = np.concatenate([[0], np.cumsum(seg)])
    L = float(cum[-1])
    if L <= 0:
        return []
    runs = _glyph_runs(text, font_path, size_mm, letter_spacing_mm)
    total = sum(a for _, a in runs)
    if closed:
        k = int(np.argmax(pts[:-1, 1] if not flip else -pts[:-1, 1]))   # höchster (bzw. tiefster) Punkt
        ref = float(cum[k])
        s0 = {"center": ref - total / 2, "right": ref - total}.get(align, ref) + start_offset
    elif align == "center":
        s0 = (L - total) / 2 + start_offset
    elif align == "right":
        s0 = L - total + start_offset
    else:
        s0 = start_offset

    def at(sv):
        if closed:
            sv %= L
        sv = min(max(sv, 0.0), L)
        i = int(np.searchsorted(cum, sv, side="right") - 1)
        i = min(max(i, 0), len(pts) - 2)
        t = 0.0 if seg[i] < 1e-12 else (sv - cum[i]) / seg[i]
        return pts[i] + (pts[i + 1] - pts[i]) * t

    out = []
    x = s0
    for polys, adv in runs:
        mid = x + adv / 2
        if not closed and (mid < 0 or mid > L):
            x += adv
            continue
        p = at(mid)
        d = at(mid + max(adv / 2, 0.5)) - at(mid - max(adv / 2, 0.5))     # Richtung über die Glyphenbreite
        ang = math.degrees(math.atan2(d[1], d[0])) if np.hypot(*d) > 1e-9 else 0.0
        nrm = np.array([-math.sin(math.radians(ang)), math.cos(math.radians(ang))])
        base = p + nrm * distance
        m = g.translate(*base) @ g.rotate(ang) @ g.translate(-adv / 2, 0)
        out += [q.transformed(m) for q in polys]
        x += adv
    return out
