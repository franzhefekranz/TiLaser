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
HINGE_STYLES = {"straight": "Normal (gerade Schlitze)", "triple": "Dreifach versetzt (weicher)",
                "wave": "Wellenschlitze (weicher, dekorativ)"}


def living_hinge(region, slit: float = 20.0, bridge: float = 3.0, spacing: float = 1.5, margin: float = 3.0,
                 angle: float = 0.0, style: str = "straight") -> list[g.Polyline]:
    """Versetzte Schnittlinien. Biegeachse parallel zu den Schlitzen (angle 0 = waagerechte Schlitze).

    Die Reihen liegen mittig in der Fläche. Die äußeren Reihen und jede zweite dazwischen halten den
    Randabstand ein (geschlossene Reihen), die Reihen dazwischen laufen bis an den Rand durch. Die
    Schlitzlänge wird minimal angepasst, damit die geschlossenen Reihen genau aufgehen.

    straight: Reihen um eine halbe Teilung versetzt, jede Reihe spiegelsymmetrisch.
    triple:   Reihen um je eine drittel Teilung versetzt – die Stege liegen weiter auseinander, das
              Holz verdreht sich auf längerer Strecke (weicher). Punktsymmetrisch zur Mitte.
    wave:     wie straight, aber wellenförmige Schlitze (alle Wellen parallel, punktsymmetrisch).
    """
    if slit <= 0 or spacing <= 0 or bridge <= 0:
        raise ValueError("Schlitzlänge, Steg und Abstand müssen größer als 0 sein.")
    if style not in HINGE_STYLES:
        style = "straight"
    rings = _region(region)
    cx, cy = _center(rings)
    rr = _rot(rings, -angle, cx, cy)
    x0, y0, x1, y1 = g.bbox_of(rr)
    amp = 0.4 * spacing if style == "wave" else 0.0
    rows = int(math.floor((y1 - y0 - 2 * margin - 2 * amp) / spacing + 1e-9)) + 1
    if rows < 1:
        return []
    if rows % 2 == 0:                                     # ungerade Zahl: außen beidseitig geschlossene Reihen
        rows -= 1
    c = (rows - 1) // 2
    ym = (y0 + y1) / 2 + JITTER[1]
    xm = (x0 + x1) / 2
    out = []
    for k in range(rows):
        j = k - c                                         # Reihe relativ zur Mitte
        y = ym + j * spacing
        closed = k % 2 == 0                                 # äußere Reihen immer geschlossen
        phase = (j % 3) / 3 if style == "triple" else (0.0 if closed else 0.5)
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
            per = s + bridge
            first = a + margin + s / 2 + phase * per       # Mitte eines Schlitzes dieser Reihe
            first -= math.ceil((first - a + s / 2) / per) * per
            lo, hi = (a + margin, b - margin) if closed else (a, b)
            m = first
            while m - s / 2 < b:
                u, v = max(m - s / 2, lo), min(m + s / 2, hi)
                m += per
                if v - u < max(1.0, min(bridge, s / 3)):
                    continue
                if style == "wave":
                    xs = np.linspace(u, v, max(8, int(math.ceil((v - u) / 0.25)) + 1))
                    pts = np.c_[xs, y + amp * np.sin(2 * math.pi * (xs - xm) / (per / 2))]
                else:
                    pts = np.array([[u, y], [v, y]])
                if closed:                                # schräge Ränder: Randabstand auch seitlich/oben
                    pts = _trim_margin(pts, rr, margin, bridge)
                    if pts is not None:
                        out.append(g.Polyline(pts, False))
                elif style == "wave":                     # Welle darf nicht über den Rand hinaus
                    out += [q for q in clip_lines([g.Polyline(pts, False)], rr)
                            if len(q.pts) >= 2 and np.hypot(*(q.pts[-1] - q.pts[0])) >= 1.0]
                else:
                    out.append(g.Polyline(pts, False))
    return _rot(out, angle, cx, cy)


def _trim_margin(line, rr, margin, min_len):
    """Längstes Stück des Linienzugs, das mindestens margin von allen Kanten entfernt ist."""
    if len(line) == 2:
        a, b = line
        n = max(2, int(math.ceil(np.hypot(*(b - a)) / 0.1)) + 1)
        pts = a + (b - a) * np.linspace(0, 1, n)[:, None]
    else:
        pts = line
    ok = distance_to_edges(pts, rr) >= margin - 1e-6
    if ok.all():
        return line
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
    piece = pts[[best[0], best[1] - 1]] if len(line) == 2 else pts[best[0]:best[1]]
    if len(piece) < 2 or np.hypot(*(piece[-1] - piece[0])) < max(1.0, min_len):
        return None
    return piece


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
    x0, y0, x1, y1 = g.bbox_of(rr)
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
    x0, y0, x1, y1 = g.bbox_of(rr)
    tiles = _tiles(kind, x0 + JITTER[0], y0 + JITTER[1], x1, y1, size, gap)
    res = boolean(rr, tiles, "intersection")
    return _rot(res, angle, cx, cy)


# ---------------------------------------------------------------- Biegezone zwischen zwei Kanten

def bend_between(closed_a, closed_b, moving, target, radius: float, angle_deg: float, thickness: float,
                 slit: float = 20.0, bridge: float = 3.0, spacing: float = 1.5, margin: float = 3.0,
                 style: str = "straight"):
    """Zwei Teile über eine Biegezone verbinden.

    ``moving``/``target`` sind Kanten (edgealign.Edge) von Teil B bzw. A. Teil B wird so gedreht und
    verschoben, dass seine Kante der Kante von A im Abstand der Biegelänge gegenüberliegt; A, der Streifen
    dazwischen und B werden vereinigt, der Streifen bekommt das Biegescharnier-Muster.
    Rückgabe: (Matrix für B, vereinigte Ringe, Schlitze, Biegelänge, Breite der Biegezone)."""
    from .clip import boolean
    from .edgealign import align_matrix, bend_length, strip_between, tidy_ring
    L = bend_length(radius, angle_deg, thickness)
    if L < 2 * spacing:
        raise ValueError(f"Die Biegezone wäre nur {L:.1f} mm lang – Radius oder Winkel vergrößern bzw. den "
                         "Reihenabstand verkleinern.")
    m = align_matrix(moving, target, "center", gap=L)
    ma, mb = g.apply_matrix(m, np.array([moving.a, moving.b]))
    # minimal schmaler und etwas überlappend: deckungsgleiche Kanten bringen das Vereinigen durcheinander
    strip, width = strip_between(target, ma, mb, L, extend=0.02, inset=0.005)
    if strip is None or width < max(5.0, 2 * margin + 1):
        raise ValueError("Die beiden Kanten überdecken sich nach dem Ausrichten nicht ausreichend.")
    rb = [g.Polyline(g.apply_matrix(m, np.asarray(getattr(p, "pts", p), float)), True) for p in closed_b]
    ra = [g.Polyline(np.asarray(getattr(p, "pts", p), float), True) for p in closed_a]
    merged = boolean(boolean(ra, [g.Polyline(strip, True)], "union"), rb, "union")
    if not merged:
        raise ValueError("Die Teile ließen sich nicht verbinden.")
    # Die Überlappung von 0,02/0,005 mm wieder herausnehmen: Punkte auf die genauen Ecken von A, B und dem
    # Streifen ziehen – sonst bleibt an den Streifenenden eine minimal schräge statt einer geraden Kante.
    exact, _ = strip_between(target, ma, mb, L)
    refs = np.vstack([np.asarray(p.pts, float) for p in ra + rb] + ([exact] if exact is not None else []))

    def snap(pts):
        pts = np.array(pts, float)
        for i, q in enumerate(pts):
            d = np.hypot(*(refs - q).T)
            j = int(np.argmin(d))
            if d[j] < 0.05:
                pts[i] = refs[j]
        return pts
    merged = [g.Polyline(tidy_ring(snap(tidy_ring(p.pts))), True) for p in merged]
    region, _ = strip_between(target, ma, mb, L, extend=margin - spacing / 2)
    ang = float(np.degrees(np.arctan2(*(target.b - target.a)[::-1])))
    slits = living_hinge([g.Polyline(region, True)], slit, bridge, spacing, margin, ang, style)
    return m, merged, slits, L, width
