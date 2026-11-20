"""Übersichtsbilder der Muster und Generatoren für die Anleitung (nur Pillow, ohne Qt).

    python tools/make_muster.py            # schreibt docs/screenshots/muster_*.png
    python tools/make_muster.py en         # englische Beschriftung -> docs/screenshots/en/muster_*.png
"""
from __future__ import annotations

import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tilaser.core import geometry as g                      # noqa: E402
from tilaser.core import i18n                               # noqa: E402
from tilaser.core.i18n import tr as _t                       # noqa: E402
from tilaser.core import patterns as pt                     # noqa: E402
from tilaser.core.joints import finger_joint, puzzle_joint  # noqa: E402
from tilaser.core.shapes_gen import gear_pair, jigsaw, stencil_bridges   # noqa: E402
from tilaser.core.text_import import default_font, text_to_polylines     # noqa: E402

LANG = sys.argv[1] if len(sys.argv) > 1 else "de"
OUT = os.path.join(ROOT, "docs", "screenshots", *([] if LANG == "de" else [LANG]))
HOLZ, BRAND, SCHNITT, TEXT = (226, 205, 166), (74, 44, 23), (200, 30, 30), (46, 55, 66)


def _font(size):
    for name in ("DejaVuSans.ttf", "Arial.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def tile(polys, region, label, fill=True, size=(300, 230), sc=2.2, ox=0.0, oy=0.0):
    im = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(im)
    bb = g.bbox(region + polys)
    cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2

    def tr(p):
        return [(size[0] / 2 + (x - cx) * sc + ox, size[1] / 2 - 12 - (y - cy) * sc + oy) for x, y in p]
    for r in region:
        d.polygon(tr(r.pts), fill=HOLZ)
    for r in region:
        if len(region) > 1 and r is not region[0]:
            d.polygon(tr(r.pts), fill="white")
    for p in polys:
        pts = tr(p.pts)
        if fill and p.closed:
            d.polygon(pts, fill=BRAND)
        else:
            d.line(pts + ([pts[0]] if p.closed else []), fill=SCHNITT, width=2)
    for r in region:
        pts = tr(r.pts)
        d.line(pts + [pts[0]], fill=TEXT, width=1)
    d.text((size[0] / 2, size[1] - 14), _t(label), fill=TEXT, font=_font(17), anchor="mm")
    return im


def sheet(tiles, cols, name):
    w, h = tiles[0].size
    rows = (len(tiles) + cols - 1) // cols
    out = Image.new("RGB", (w * cols, h * rows), "white")
    for i, t in enumerate(tiles):
        out.paste(t, ((i % cols) * w, (i // cols) * h))
    out.save(os.path.join(OUT, name), optimize=True)


def main():
    i18n.set_language(LANG)
    os.makedirs(OUT, exist_ok=True)
    disc = [g.Polyline(g.circle_points(0, 0, 45), True)]
    sheet([tile(pt.engrave_pattern(disc, k, 10, 1.2), disc, label) for k, label in pt.ENGRAVE_PATTERNS.items()],
          3, "muster_gravur.png")
    rect = [g.Polyline(np.array([[0, 0], [110, 0], [110, 70], [0, 70]], float), True)]
    hinge = tile(pt.living_hinge(rect, 20, 3, 2.0, 4), rect, "Biegescharnier", False, sc=2.3)
    holes = tile(pt.hole_pattern(rect, "hex", 7, 2, "offset", 4), rect, "Lochmuster Sechseck", False, sc=2.3)
    slots = tile(pt.hole_pattern(rect, "slot", 5, 3, "offset", 5, slot_len=20), rect, "Lüftungsschlitze", False,
                 sc=2.3)
    a = [np.array([[0, 0], [80, 0], [80, 40], [0, 40.]])]
    b = [np.array([[80, 70], [0, 70], [0, 40], [80, 40.]])]
    ra, rb, _ = finger_joint(a, b, 4, 12, "tabs", dogbone=1.5)
    shift = np.array([0, 12.0])
    joint = tile([g.Polyline(r, True) for r in ra] + [g.Polyline(r + shift, True) for r in rb], [],
                 "Fingerzinken mit Dogbones", False, sc=2.0)
    ga, gb, _ = gear_pair(2, 14, 22, bore1=5, bore2=8)
    gears = tile(ga + gb, [], "Zahnradpaar", False, sc=2.2)
    frame, lines = jigsaw(110, 70, 4, 3, seed=11, radius=4)
    puzzle = tile([frame] + lines, [], "Puzzle", False, sc=2.3)
    txt = text_to_polylines("OBA 8", default_font(), 34) if default_font() else []
    st, _ = stencil_bridges(txt, 2.0) if txt else ([], 0)
    sten = tile(st, [], "Schablonen-Stege", False, sc=1.9)
    sheet([hinge, holes, slots, joint, gears, puzzle, sten], 3, "muster_generatoren.png")
    joint_types()
    hinge_types()
    puzzle_types()
    print("Bilder in", OUT)


def joint_types():
    """Vergleich der beiden Zinkenarten: Teile wie gezeichnet, farbig gefüllt."""
    a = [np.array([[0, 0], [80, 0], [80, 40], [0, 40.]])]
    b = [np.array([[0, 40], [80, 40], [80, 70], [0, 70.]])]
    im = Image.new("RGB", (900, 400), "white")
    d = ImageDraw.Draw(im)
    sc = 3.6
    for i, (kind, label) in enumerate((("tabs", "Ineinandergreifend"), ("corner", "Eckverbindung"))):
        ra, rb, _ = finger_joint(a, b, 4, 12, kind, dogbone=0)
        ox = 60 + i * 450
        for rs, col in ((ra, HOLZ), (rb, (150, 190, 230))):
            for r in rs:
                d.polygon([(x * sc + ox, 330 - y * sc) for x, y in r], fill=col, outline=TEXT)
        d.text((ox + 40 * sc, 370), _t(label), fill=TEXT, font=_font(18), anchor="mm")
    im.save(os.path.join(OUT, "zinken_arten.png"), optimize=True)


def puzzle_types():
    """Puzzle-Verbindung: Nasen an einem Teil und abwechselnd, Teile wie gezeichnet, farbig gefüllt."""
    a = [np.array([[0, 0], [80, 0], [80, 35], [0, 35.]])]
    b = [np.array([[0, 35], [80, 35], [80, 70], [0, 70.]])]
    im = Image.new("RGB", (900, 400), "white")
    d = ImageDraw.Draw(im)
    sc = 3.6
    for i, (alt, label) in enumerate(((False, "Nasen an einem Teil"), (True, "Nasen abwechselnd"))):
        ra, rb, _ = puzzle_joint(a, b, 12, 0, 0.0, alt)
        ox = 60 + i * 450
        for rs, col in ((ra, HOLZ), (rb, (150, 190, 230))):
            for r in rs:
                d.polygon([(x * sc + ox, 330 - y * sc) for x, y in r], fill=col, outline=TEXT)
        d.text((ox + 40 * sc, 370), _t(label), fill=TEXT, font=_font(18), anchor="mm")
    im.save(os.path.join(OUT, "puzzle_verbindung.png"), optimize=True)


def hinge_types():
    """Die drei Arten des Biegescharniers nebeneinander."""
    rect = [g.Polyline(np.array([[0, 0], [70, 0], [70, 40], [0, 40]], float), True)]
    tiles = [tile(pt.living_hinge(rect, 14, 2.5, 2.0, 3, 0, k), rect, label, False, size=(300, 200), sc=3.8)
             for k, label in (("straight", "Normal"), ("triple", "Dreifach versetzt"), ("wave", "Wellenschlitze"))]
    sheet(tiles, 3, "scharnier_arten.png")


if __name__ == "__main__":
    main()
