"""Muster in einer Fläche: Biegescharnier, Lochmuster/Lüftungsgitter und Gravurmuster.

Alle Funktionen bekommen die Fläche als Liste geschlossener Polylinien (Weltkoordinaten,
Even-Odd) und liefern neue Polylinien in Weltkoordinaten.
"""
from __future__ import annotations

import math

import numpy as np

from . import geometry as g
from .clip import _rings, boolean, chain, clip_lines, distance_to_edges, inside

JITTER = np.array([1.37e-4, 2.71e-4])     # Raster minimal verschieben (keine deckungsgleichen Kanten)


def _rot(polys, deg, cx, cy):
    if not deg:
        return list(polys)
    m = g.translate(cx, cy) @ g.rotate(deg) @ g.translate(-cx, -cy)
    return [g.Polyline(g.apply_matrix(m, p.pts), p.closed) for p in polys]


def _region(region):
    rings = [g.Polyline(r, True) for r in _rings(region)]
    if not rings:
        raise ValueError("Bitte eine geschlossene Kontur (Fläche) auswählen.")
    return rings


def _center(rings):
    bb = g.bbox(rings)
    return (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2


# --------------------------------------------------------------------------- Biegescharnier
HINGE_STYLES = {"straight": "Gerade Schlitze", "double": "Doppelte Schlitze (weicher)"}


def living_hinge(region, slit: float = 20.0, bridge: float = 3.0, spacing: float = 1.5, margin: float = 3.0,
                 angle: float = 0.0, style: str = "straight") -> list[g.Polyline]:
    """Versetzte Schnittlinien. Biegeachse parallel zu den Schlitzen (angle 0 = waagerechte Schlitze).

    Symmetrisch aufgebaut: Die Reihen liegen mittig in der Fläche, die äußeren Reihen und jede zweite
    dazwischen halten den Randabstand ein (geschlossene Reihen). Die Reihen dazwischen sind um eine halbe
    Teilung versetzt – ihre Stege liegen genau mittig unter den Schlitzen der Nachbarreihen – und laufen
    bis an den Rand durch. Die Schlitzlänge wird minimal angepasst, damit jede Reihe aufgeht.
    """
    if slit <= 0 or spacing <= 0 or bridge <= 0:
        raise ValueError("Schlitzlänge, Steg und Abstand müssen größer als 0 sein.")
    rings = _region(region)
    cx, cy = _center(rings)
    rr = _rot(rings, -angle, cx, cy)
    x0, y0, x1, y1 = g.bbox(rr)
    rows = int(math.floor((y1 - y0 - 2 * margin) / spacing + 1e-9)) + 1
    if rows < 1:
        return []
    if rows % 2 == 0:                                     # ungerade Zahl: außen beidseitig geschlossene Reihen
        rows -= 1
    ym = (y0 + y1) / 2 + JITTER[1]
    out = []
    for k in range(rows):
        y = ym + (k - (rows - 1) / 2) * spacing
        closed = k % 2 == 0
        span = [g.Polyline(np.array([[x0 - 1, y], [x1 + 1, y]]), False)]
        for piece in clip_lines(span, rr):
            a, b = sorted((float(piece.pts[0][0]), float(piece.pts[-1][0])))
            usable = b - a - 2 * margin
            if usable <= 0:
                continue
            n = max(1, int(round((usable + bridge) / (slit + bridge))))
            s = (usable - (n - 1) * bridge) / n
            if s <= 0:
                n, s = 1, usable
            starts = a + margin + np.arange(n) * (s + bridge)
            if closed:
                segs = [(x, x + s) for x in starts]
            else:
                mids = starts + s / 2
                cuts = [a] + [c for m in mids for c in (m - bridge / 2, m + bridge / 2)] + [b]
                segs = [(cuts[i], cuts[i + 1]) for i in range(0, len(cuts), 2)]
            for u, v in segs:
                if v - u < 0.3:
                    continue
                pts = np.array([[u, y], [v, y]])
                if closed:                                # schräge Ränder: Randabstand auch seitlich/oben
                    pts = _trim_margin(pts, rr, margin, bridge)
                    if pts is None:
                        continue
                out.append(g.Polyline(pts, False))
    return _rot(out, angle, cx, cy)


def _trim_margin(seg, rr, margin, min_len):
    """Längstes Stück der Strecke, das mindestens margin von allen Kanten entfernt ist."""
    a, b = seg
    n = max(2, int(math.ceil(np.hypot(*(b - a)) / 0.1)) + 1)
    pts = a + (b - a) * np.linspace(0, 1, n)[:, None]
    ok = distance_to_edges(pts, rr) >= margin - 1e-6
    if ok.all():
        return seg
    best, start = None, None
    for i, v in enumerate(list(ok) + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if best is None or i - start > best[1] - best[0]:
                best = (start, i)
            start = None
    if best is None:
        return None
    piece = pts[[best[0], best[1] - 1]]
    return piece if np.hypot(*(piece[1] - piece[0])) >= max(1.0, min_len) else None


# --------------------------------------------------------------------------- Lochmuster
HOLE_SHAPES = {"circle": "Kreis", "hex": "Sechseck", "square": "Quadrat", "slot": "Langloch"}
HOLE_LAYOUTS = {"offset": "Versetzt (Waben)", "grid": "Raster"}


def _hole(shape, cx, cy, size, slot_len):
    r = size / 2
    if shape == "circle":
        return g.circle_points(cx, cy, r)
    if shape == "hex":                                     # size = Schlüsselweite, Spitze oben
        R = r / math.cos(math.pi / 6)
        a = np.radians(np.arange(6) * 60 + 90)
        return np.c_[cx + R * np.cos(a), cy + R * np.sin(a)]
    if shape == "square":
        return np.array([[cx - r, cy - r], [cx + r, cy - r], [cx + r, cy + r], [cx - r, cy + r]])
    L = max(slot_len, size)                                # Langloch waagerecht
    h = (L - size) / 2
    right = g.ellipse_arc_points(cx + h, cy, r, r, 0, -math.pi / 2, math.pi / 2)
    left = g.ellipse_arc_points(cx - h, cy, r, r, 0, math.pi / 2, 3 * math.pi / 2)
    return np.vstack([right, left])


def hole_pattern(region, shape: str = "circle", size: float = 5.0, web: float = 2.0, layout: str = "offset",
                 margin: float = 3.0, slot_len: float = 15.0, angle: float = 0.0) -> list[g.Polyline]:
    """Ganze Löcher, die vollständig und mit Randabstand in der Fläche liegen."""
    if size <= 0 or web < 0:
        raise ValueError("Lochgröße muss größer als 0 sein.")
    rings = _region(region)
    cx, cy = _center(rings)
    rr = _rot(rings, -angle, cx, cy)
    x0, y0, x1, y1 = g.bbox(rr)
    px = (max(slot_len, size) if shape == "slot" else size) + web
    if shape == "hex" and layout == "offset":
        py = (size + web) * math.sqrt(3) / 2
        px = size + web
    elif layout == "offset":
        py = (size + web) * (math.sqrt(3) / 2 if shape == "circle" else 1.0)
    else:
        py = size + web
    nx = int((x1 - x0) / px) + 3
    ny = int((y1 - y0) / py) + 3
    centers = []
    for j in range(-ny // 2 - 1, ny // 2 + 2):
        off = px / 2 if (layout == "offset" and j % 2) else 0.0
        for i in range(-nx // 2 - 1, nx // 2 + 2):
            centers.append((cx + i * px + off, cy + j * py))
    centers = np.array(centers) + JITTER
    keep = inside(centers, rr)
    centers = centers[keep]
    out = []
    shapes = [_hole(shape, x, y, size, slot_len) for x, y in centers]
    if not shapes:
        return []
    allpts = np.vstack(shapes)
    idx = np.repeat(np.arange(len(shapes)), [len(s) for s in shapes])
    ok_in = inside(allpts, rr)
    ok_d = distance_to_edges(allpts, rr) >= margin - 1e-9
    good = np.ones(len(shapes), bool)
    np.logical_and.at(good, idx, ok_in & ok_d)
    for s, ok in zip(shapes, good):
        if ok:
            out.append(g.Polyline(s, True))
    return _rot(out, angle, cx, cy)


# --------------------------------------------------------------------------- Gravurmuster
ENGRAVE_PATTERNS = {"houndstooth": "Hahnentritt", "herringbone": "Fischgrät", "checker": "Schachbrett",
                    "diamond": "Rauten", "dots": "Punkte", "honeycomb": "Waben", "brick": "Ziegel",
                    "stripes": "Streifen", "waves": "Wellen"}


def bitmap_rings(mask: np.ndarray, px: float, x0: float, y0: float) -> list[g.Polyline]:
    """Umrisse der gesetzten Pixel (Zeile 0 unten) als Ringe."""
    m = np.pad(mask.astype(bool), 1)
    pieces = []
    r, c = np.nonzero(m[1:, :] != m[:-1, :])          # waagerechte Kante zwischen Originalzeile r-1 und r
    for y, xa in zip(y0 + r * px, x0 + (c - 1) * px):
        pieces.append(np.array([[xa, y], [xa + px, y]]))
    r, c = np.nonzero(m[:, 1:] != m[:, :-1])          # senkrechte Kante zwischen Originalspalte c-1 und c
    for x, ya in zip(x0 + c * px, y0 + (r - 1) * px):
        pieces.append(np.array([[x, ya], [x, ya + px]]))
    return [p for p in chain(pieces, tol=px * 1e-3) if p.closed]


def _textile(kind, nx, ny):
    j = np.arange(nx)[None, :]
    i = np.arange(ny)[:, None]
    if kind == "houndstooth":                 # 2/2-Köper, je 4 dunkle und 4 helle Fäden
        warp = (j % 8) < 4
        weft = (i % 8) < 4
        up = ((i + j) % 4) < 2
        return np.where(up, warp, weft)
    # Fischgrät: Köperdiagonale wechselt alle 8 Spalten die Richtung
    blk = (j // 8) % 2
    jj = j % 8
    return np.where(blk == 0, ((i + jj) % 4) < 2, ((i - jj) % 4) < 2)


def _tiles(kind, x0, y0, x1, y1, s, gap):
    """Flächen des Musters (axial), die das Rechteck vollständig überdecken."""
    out = []
    W, H = x1 - x0, y1 - y0
    if kind in ("houndstooth", "herringbone"):
        u = s / 8
        nx, ny = int(W / u) + 10, int(H / u) + 10
        mask = _textile(kind, nx, ny)
        return bitmap_rings(mask, u, x0 - 4 * u, y0 - 4 * u)
    nx, ny = int(W / s) + 3, int(H / s) + 3

    def rect(x, y, w, h):
        return g.Polyline(np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]]), True)
    if kind == "checker":
        for j in range(-1, ny):
            for i in range(-1, nx):
                if (i + j) % 2 == 0:
                    out.append(rect(x0 + i * s, y0 + j * s, s, s))
    elif kind == "diamond":
        h = max(0.05, s / 2 - gap / math.sqrt(2))
        for j in range(-1, ny):
            for i in range(-1, nx):
                for cx, cy in ((x0 + i * s, y0 + j * s), (x0 + (i + .5) * s, y0 + (j + .5) * s)):
                    out.append(g.Polyline(np.array([[cx, cy - h], [cx + h, cy], [cx, cy + h], [cx - h, cy]]), True))
    elif kind == "dots":
        r = max(0.05, (s - gap) / 2)
        py = s * math.sqrt(3) / 2
        for j in range(-1, int(H / py) + 3):
            for i in range(-1, nx):
                out.append(g.Polyline(g.circle_points(x0 + (i + (j % 2) / 2) * s, y0 + j * py, r), True))
    elif kind == "honeycomb":
        R = max(0.05, (s - gap) / 2) / math.cos(math.pi / 6)
        py = s * math.sqrt(3) / 2
        a = np.radians(np.arange(6) * 60 + 90)
        for j in range(-1, int(H / py) + 3):
            for i in range(-1, nx):
                cx, cy = x0 + (i + (j % 2) / 2) * s, y0 + j * py
                out.append(g.Polyline(np.c_[cx + R * np.cos(a), cy + R * np.sin(a)], True))
    elif kind == "brick":
        h = s / 2
        for j in range(-1, int(H / h) + 3):
            for i in range(-1, nx):
                out.append(rect(x0 + (i + (j % 2) / 2) * s + gap / 2, y0 + j * h + gap / 2,
                                max(0.05, s - gap), max(0.05, h - gap)))
    elif kind == "stripes":
        for j in range(-1, ny):
            out.append(rect(x0 - s, y0 + j * s, W + 2 * s, max(0.05, s / 2)))
    elif kind == "waves":
        amp, th = s / 4, max(0.05, s / 2 - gap / 2)
        xs = np.linspace(x0 - s, x1 + s, max(8, int((W + 2 * s) / (s / 16)) + 1))
        for j in range(-2, ny + 1):
            base = y0 + j * s + amp * np.sin(2 * math.pi * xs / s)
            out.append(g.Polyline(np.vstack([np.c_[xs, base], np.c_[xs[::-1], base[::-1] + th]]), True))
    else:
        raise ValueError(f"Unbekanntes Muster: {kind}")
    return out


def engrave_pattern(region, kind: str = "houndstooth", size: float = 8.0, gap: float = 1.0,
                    angle: float = 0.0) -> list[g.Polyline]:
    """Musterflächen innerhalb der Fläche (für eine Füll-Ebene)."""
    if size <= 0.5:
        raise ValueError("Die Mustergröße muss größer als 0,5 mm sein.")
    rings = _region(region)
    cx, cy = _center(rings)
    rr = _rot(rings, -angle, cx, cy)
    x0, y0, x1, y1 = g.bbox(rr)
    tiles = _tiles(kind, x0 + JITTER[0], y0 + JITTER[1], x1, y1, size, gap)
    res = boolean(rr, tiles, "intersection")
    return _rot(res, angle, cx, cy)
