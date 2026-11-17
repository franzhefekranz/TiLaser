"""Text -> Konturen über TrueType/OpenType-Schriften (fontTools)."""
from __future__ import annotations

import glob
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
        def __init__(self, glyphset):
            super().__init__(glyphset)
            self.polys, self.cur = [], []

        def _moveTo(self, p):
            self._flush()
            self.cur = [np.array(p, float)[None, :]]

        def _lineTo(self, p):
            self.cur.append(np.array(p, float)[None, :])

        def _curveToOne(self, p1, p2, p3):
            p0 = self._getCurrentPoint()
            self.cur.append(g.flatten_cubic(p0, p1, p2, p3, utol))

        def _qCurveToOne(self, p1, p2):
            p0 = self._getCurrentPoint()
            self.cur.append(g.flatten_quad(p0, p1, p2, utol))

        def _closePath(self):
            self._flush(True)

        def _endPath(self):
            self._flush(False)

        def _flush(self, closed=True):
            if self.cur:
                pts = np.vstack(self.cur)
                if len(pts) > 1 and np.allclose(pts[0], pts[-1]):
                    pts = pts[:-1]
                if len(pts) >= 2:
                    self.polys.append(g.Polyline(pts, closed))
            self.cur = []

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
            for p in pen.polys:
                polys_line.append(g.Polyline(p.pts * s + [x, 0], True))
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
        out += [g.Polyline(p.pts + [dx, dy], True) for p in polys_line]
    font.close()
    return out
