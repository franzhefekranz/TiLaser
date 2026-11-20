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
from tilaser.core.joints import (HammerSpec, KnuckleSpec, SnapSpec, finger_joint, hammer_joint,  # noqa: E402
                                 hammer_tenon_profile, knuckle_hinge, knuckle_hole, knuckle_plate, puzzle_joint,
                                 snap_lock, snap_tab_profile)
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
    snap_types()
    hammer_types()
    knuckle_types()
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


def snap_types():
    """Snap-Lock: links die Teile wie erzeugt, rechts eine Lasche im Schnitt (eingerastet)."""
    a = [np.array([[0, 0], [80, 0], [80, 30], [0, 30.]])]
    b = [np.array([[0, 30], [80, 30], [80, 60], [0, 60.]])]
    spec = SnapSpec(thickness=4, width=10, barb=0.7, slit=1.6)
    ra, rb, _, _ = snap_lock(a, b, spec)
    im = Image.new("RGB", (900, 420), "white")
    d = ImageDraw.Draw(im)
    sc, ox = 3.6, 60
    d.polygon([(x * sc + ox, 360 - (y + 12) * sc) for x, y in ra[0]], fill=HOLZ, outline=TEXT)
    d.polygon([(x * sc + ox, 360 - (y + 12) * sc) for x, y in rb[0]], fill=(150, 190, 230), outline=TEXT)
    for r in rb[1:]:
        d.polygon([(x * sc + ox, 360 - (y + 12) * sc) for x, y in r], fill="white", outline=TEXT)
    d.text((ox + 40 * sc, 395), _t("Teile wie erzeugt"), fill=TEXT, font=_font(18), anchor="mm")
    # Schnitt: Teil A (Lasche) steckt senkrecht durch Teil B
    sc2, cx, base = 9.0, 690, 300
    prof = snap_tab_profile(0.0, spec)
    tab = [(cx + s * sc2, base - t * sc2) for s, t in prof]
    body = [(cx - 12 * sc2, base)] + tab + [(cx + 12 * sc2, base), (cx + 12 * sc2, base + 8 * sc2),
                                           (cx - 12 * sc2, base + 8 * sc2)]
    d.polygon(body, fill=HOLZ, outline=TEXT)
    t = spec.thickness
    d.rectangle([cx - 20 * sc2, base - t * sc2, cx - (spec.width + spec.clearance) / 2 * sc2, base], fill=(150, 190, 230),
                outline=TEXT)
    d.rectangle([cx + (spec.width + spec.clearance) / 2 * sc2, base - t * sc2, cx + 20 * sc2, base], fill=(150, 190, 230),
                outline=TEXT)
    d.text((cx, 395), _t("Eingerastet (Schnitt)"), fill=TEXT, font=_font(18), anchor="mm")
    im.save(os.path.join(OUT, "snap_lock.png"), optimize=True)


def hammer_types():
    """Hammerverbindung: links die Teile wie erzeugt, Mitte der Zapfen vergrößert, rechts eingeschlagen (Schnitt)."""
    a = [np.array([[0, 0], [70, 0], [70, 34], [0, 34.]])]
    b = [np.array([[0, 34], [70, 34], [70, 62], [0, 62.]])]
    spec = HammerSpec()
    ra, rb, _, shift = hammer_joint(a, b, spec)
    im = Image.new("RGB", (1000, 460), "white")
    d = ImageDraw.Draw(im)
    sc, ox, oy = 3.4, 30, 330

    def P(r, s=sc, x0=ox, y0=oy, cx=0.0, cy=0.0):
        return [((x - cx) * s + x0, y0 - (y - cy) * s) for x, y in r]
    d.polygon(P(ra[0]), fill=HOLZ, outline=TEXT)
    for r in ra[1:]:
        d.polygon(P(r), fill="white", outline=TEXT)
    d.polygon(P(rb[0]), fill=(150, 190, 230), outline=TEXT)
    for r in rb[1:]:
        d.polygon(P(r), fill="white", outline=TEXT)
    d.text((ox + 35 * sc, 430), _t("Teile wie erzeugt"), fill=TEXT, font=_font(18), anchor="mm")
    # vergrößert: Zapfen mit Keil und Stegen (nur Linien, wie geschnitten)
    cx, cy = 35.0, 34 + float(shift[1])
    zoom = Image.new("RGB", (300, 330), "white")
    dz = ImageDraw.Draw(zoom)
    for r in ra:
        dz.line(P(np.vstack([r, r[:1]]), 7.0, 150, 230, cx, cy), fill=(200, 40, 40), width=2)
    dz.rectangle([0, 0, 299, 329], outline=(200, 200, 200))
    im.paste(zoom, (350, 75))
    d.text((500, 430), _t("Zapfen mit Keil und Stegen"), fill=TEXT, font=_font(18), anchor="mm")
    # Schnitt nach dem Einschlagen
    sc2, cx2, base = 6.0, 840, 270
    H = spec.thickness + spec.protrude
    edge, _ = hammer_tenon_profile(0.0, spec)
    tenon = [(s, t) for s, t in edge if t <= H + 1e-9]               # ohne Keilkopf
    body = [(cx2 - 22 * sc2, base)] + [(cx2 + s * sc2, base - t * sc2) for s, t in tenon] + \
        [(cx2 + 22 * sc2, base), (cx2 + 22 * sc2, base + 21 * sc2), (cx2 - 22 * sc2, base + 21 * sc2)]
    d.polygon(body, fill=HOLZ, outline=TEXT)
    t, hw = spec.thickness, spec.width / 2
    for x0, x1 in ((-26, -hw), (hw, 26)):
        d.rectangle([cx2 + x0 * sc2, base - (t + spec.clearance) * sc2, cx2 + x1 * sc2, base], fill=(150, 190, 230),
                    outline=TEXT)
    top = [p for p in edge if p[1] > H + 1e-9]
    wb = -spec.depth + 1.0
    half_top = abs(top[0][0])
    wedge = [(-half_top, H), (half_top, H), (half_top - spec.spread / 2 - spec.taper / 2 * (H - wb), wb),
             (-half_top + spec.spread / 2 + spec.taper / 2 * (H - wb), wb)]
    d.polygon([(cx2 + s * sc2, base - y * sc2) for s, y in wedge], fill=(200, 160, 110), outline=TEXT)
    d.text((cx2, 430), _t("Keil eingeschlagen (Schnitt)"), fill=TEXT, font=_font(18), anchor="mm")
    im.save(os.path.join(OUT, "hammerverbindung.png"), optimize=True)


def knuckle_types():
    """Knöchelscharnier: links die Teile, Mitte quer zur Achse gesehen, rechts von oben (zusammengesteckt)."""
    spec = KnuckleSpec(knuckles=7)
    parts = knuckle_hinge(spec)
    im = Image.new("RGB", (1100, 430), "white")
    d = ImageDraw.Draw(im)
    allp = np.vstack([r for _, rs in parts for r in rs])
    x0, y1 = allp[:, 0].min(), allp[:, 1].max()
    sc = min(330 / np.ptp(allp[:, 0]), 330 / np.ptp(allp[:, 1]))
    for _, rs in parts:
        d.polygon([(20 + (x - x0) * sc, 30 + (y1 - y) * sc) for x, y in rs[0]], fill=HOLZ, outline=TEXT)
        for r in rs[1:]:
            d.polygon([(20 + (x - x0) * sc, 30 + (y1 - y) * sc) for x, y in r], fill="white", outline=TEXT)
    d.text((190, 400), _t("Teile"), fill=TEXT, font=_font(18), anchor="mm")
    # quer zur Achse: Blatt A links, Blatt B rechts, je eine Scheibe, Stift im Auge
    t = spec.thickness
    R = knuckle_hole(spec) / 2 + spec.wall
    s2, cx, cy = 9.0, 560, 190
    plate, hole = knuckle_plate(spec)

    def Q(pts, mirror=False):
        return [(cx + (-x if mirror else x) * s2, cy - y * s2) for x, y in pts]
    for mir, col in ((True, (150, 190, 230)), (False, HOLZ)):
        lw = spec.leaf_width
        leaf = [(-lw, -R), (-0.5, -R), (-0.5, -R - t), (-lw, -R - t)]
        d.polygon(Q(leaf, mir), fill=col, outline=TEXT)
        d.polygon(Q(plate, mir), fill=col, outline=TEXT)
    d.polygon(Q(hole), fill="white", outline=TEXT)
    q = t / 2
    d.polygon(Q([(-q, -q), (q, -q), (q, q), (-q, q)]), fill=(200, 160, 110), outline=TEXT)
    d.text((cx, 400), _t("Quer zur Achse"), fill=TEXT, font=_font(18), anchor="mm")
    # von oben: Scheiben abwechselnd an A (Holz) und B (blau), Achse waagerecht
    s3, ox, ay = 5.0, 830, 200
    n = spec.knuckles
    xl, xr = ox - spec.margin * s3, ox + (n * t + spec.margin) * s3
    d.rectangle([xl, ay + 0.5 * s3, xr, ay + spec.leaf_width * s3], fill=HOLZ, outline=TEXT)          # Blatt A
    d.rectangle([xl, ay - spec.leaf_width * s3, xr, ay - 0.5 * s3], fill=(150, 190, 230), outline=TEXT)  # Blatt B
    for i in range(n):
        x = ox + i * t * s3
        if i % 2 == 0:
            d.rectangle([x, ay - R * s3, x + t * s3, ay + spec.arm * s3], fill=HOLZ, outline=TEXT)
        else:
            d.rectangle([x, ay - spec.arm * s3, x + t * s3, ay + R * s3], fill=(150, 190, 230), outline=TEXT)
    d.rectangle([ox - t * s3, ay - t / 2 * s3, ox + (n + 1) * t * s3, ay + t / 2 * s3],
                fill=(200, 160, 110), outline=TEXT)
    d.text((ox + n * t * s3 / 2, 400), _t("Von oben"), fill=TEXT, font=_font(18), anchor="mm")
    im.save(os.path.join(OUT, "knoechelscharnier.png"), optimize=True)


def hinge_types():
    """Die drei Arten des Biegescharniers nebeneinander."""
    rect = [g.Polyline(np.array([[0, 0], [70, 0], [70, 40], [0, 40]], float), True)]
    tiles = [tile(pt.living_hinge(rect, 14, 2.5, 2.0, 3, 0, k), rect, label, False, size=(300, 200), sc=3.8)
             for k, label in (("straight", "Normal"), ("triple", "Dreifach versetzt"), ("wave", "Wellenschlitze"))]
    sheet(tiles, 3, "scharnier_arten.png")


if __name__ == "__main__":
    main()
