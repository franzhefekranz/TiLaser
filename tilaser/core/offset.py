"""Konturen versetzen (Offset) für Kerf-Ausgleich und Haltestege.

Verwendet pyclipper, falls installiert (robust, auch bei Selbstüberschneidungen).
Ohne pyclipper wird ein einfacher Gehrungs-Offset verwendet, der für übliche
Bauteilkonturen genügt.
"""
from __future__ import annotations

import math

import numpy as np

from . import geometry as g

try:
    import pyclipper
except ImportError:          # pragma: no cover - abhängig von der Installation
    pyclipper = None

SCALE = 10000.0              # 0,1 µm Auflösung für Clipper


def _offset_simple(pts: np.ndarray, d: float, miter_limit: float = 4.0) -> np.ndarray:
    """Gehrungs-Offset eines geschlossenen Polygons. d > 0 = nach außen."""
    p = np.asarray(pts, float)
    if np.allclose(p[0], p[-1]):
        p = p[:-1]
    # Orientierung: CCW -> Außennormale liegt rechts der Kante
    area = 0.5 * np.sum(p[:, 0] * np.roll(p[:, 1], -1) - np.roll(p[:, 0], -1) * p[:, 1])
    sign = 1.0 if area > 0 else -1.0
    out = []
    n = len(p)
    for i in range(n):
        a, b, c = p[i - 1], p[i], p[(i + 1) % n]
        e1, e2 = b - a, c - b
        l1, l2 = math.hypot(*e1), math.hypot(*e2)
        if l1 < 1e-12 or l2 < 1e-12:
            continue
        n1 = np.array([e1[1], -e1[0]]) / l1 * sign
        n2 = np.array([e2[1], -e2[0]]) / l2 * sign
        m = n1 + n2
        dot = 1 + float(np.dot(n1, n2))
        if dot < 1e-9:
            out.append(b + n1 * d)
            out.append(b + n2 * d)
            continue
        k = 2.0 / dot
        if math.sqrt(k) > miter_limit:          # zu spitz -> abschrägen
            out.append(b + n1 * d)
            out.append(b + n2 * d)
        else:
            out.append(b + m * (d * k / 2))
    return np.array(out)


def offset_closed(pts: np.ndarray, d: float) -> list[np.ndarray]:
    """Versetzt ein geschlossenes Polygon um d (mm, > 0 = größer). Kann mehrere/keine Konturen liefern."""
    if abs(d) < 1e-9:
        return [np.asarray(pts, float)]
    if pyclipper is not None:
        pco = pyclipper.PyclipperOffset(2.0, 0.25 * SCALE / 1000)
        pco.AddPath([(int(round(x * SCALE)), int(round(y * SCALE))) for x, y in pts],
                    pyclipper.JT_MITER, pyclipper.ET_CLOSEDPOLYGON)
        res = pco.Execute(d * SCALE)
        return [np.array(r, float) / SCALE for r in res if len(r) >= 3]
    p = np.asarray(pts, float)
    if d < 0 and min(np.ptp(p[:, 0]), np.ptp(p[:, 1])) <= -2 * d:
        return []                    # schmaler als der doppelte Versatz: Kontur verschwindet ganz
    r = _offset_simple(pts, d)
    if len(r) < 3:
        return []
    a0, a1 = _signed_area(pts), _signed_area(r)
    if d < 0 and (a0 * a1 <= 0 or abs(a1) >= abs(a0)):
        return []                    # Kontur kleiner als der Versatz: umgestülptes Polygon statt Leere
    return [r]


def _signed_area(pts) -> float:
    p = np.asarray(pts, float)
    return 0.5 * float(np.sum(p[:, 0] * np.roll(p[:, 1], -1) - np.roll(p[:, 0], -1) * p[:, 1]))


def apply_kerf(polys: list[g.Polyline], kerf: float, lost: list | None = None) -> list[g.Polyline]:
    """Kerf-Ausgleich: Außenkonturen um kerf/2 nach außen, Löcher um kerf/2 nach innen.

    Offene Pfade bleiben unverändert. kerf < 0 kehrt die Richtung um (z. B. für Gravuren).
    Konturen, die dabei verschwinden (kleiner als die Schnittbreite), werden an ``lost`` angehängt.
    """
    if abs(kerf) < 1e-9:
        return polys
    depth = g.nesting_depth(polys)
    out = []
    for p, dep in zip(polys, depth):
        if not p.closed or len(p.pts) < 3:
            out.append(p)
            continue
        d = kerf / 2 if dep % 2 == 0 else -kerf / 2
        rings = offset_closed(p.pts, d)
        if not rings and lost is not None:
            lost.append(p)
        for r in rings:
            out.append(g.Polyline(r, True))
    return out


def add_tabs(polys: list[g.Polyline], count: int, width: float, outer_only: bool = True,
             min_length: float = 20.0) -> list[g.Polyline]:
    """Haltestege: lässt in geschlossenen Konturen 'count' gleichmäßig verteilte Lücken
    der Breite 'width' stehen. Die Kontur zerfällt dabei in offene Teilstücke."""
    if count <= 0 or width <= 0:
        return polys
    depth = g.nesting_depth(polys) if outer_only else [0] * len(polys)
    out = []
    for p, dep in zip(polys, depth):
        L = p.length()
        if not p.closed or dep % 2 == 1 or L < max(min_length, count * width * 2):
            out.append(p)
            continue
        pts = p.loop_pts()
        seg = np.hypot(*np.diff(pts, axis=0).T)
        cum = np.concatenate([[0], np.cumsum(seg)])
        spacing = L / count
        gaps = [(spacing * (k + 0.5) - width / 2, spacing * (k + 0.5) + width / 2) for k in range(count)]
        # Brennabschnitte zwischen den Lücken
        cuts = []
        prev = gaps[-1][1] - L          # Ende der letzten Lücke (umlaufend)
        for g0, g1 in gaps:
            cuts.append((prev, g0))
            prev = g1
        for a, b in cuts:
            piece = _sub_path(pts, cum, a, b, L)
            if len(piece) >= 2:
                out.append(g.Polyline(piece, False))
    return out


def _point_at(pts, cum, s):
    i = int(np.searchsorted(cum, s, side="right") - 1)
    i = min(max(i, 0), len(pts) - 2)
    seg = cum[i + 1] - cum[i]
    t = 0.0 if seg < 1e-12 else (s - cum[i]) / seg
    return pts[i] + (pts[i + 1] - pts[i]) * t, i


def _sub_path(pts, cum, a, b, L):
    """Teilstück entlang der Kontur von Bogenlänge a bis b (a darf negativ sein = umlaufend)."""
    if a < 0:
        first = _sub_path(pts, cum, a + L, L, L)
        second = _sub_path(pts, cum, 0, b, L)
        if len(first) and len(second):
            return np.vstack([first, second[1:]])
        return first if len(first) else second
    pa, ia = _point_at(pts, cum, a)
    pb, ib = _point_at(pts, cum, b)
    mid = pts[ia + 1: ib + 1]
    return np.vstack([pa[None, :], mid, pb[None, :]])
