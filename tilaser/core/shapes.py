"""Grundformen zum Zeichnen. Alle Formen sind um (0, 0) zentriert, Maße in mm."""
from __future__ import annotations

import math

import numpy as np

from . import geometry as g


def rectangle(w: float, h: float, radius: float = 0.0) -> g.Polyline:
    w, h = abs(w), abs(h)
    r = max(0.0, min(radius, w / 2, h / 2))
    x0, y0, x1, y1 = -w / 2, -h / 2, w / 2, h / 2
    if r < 1e-9:
        return g.Polyline(np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]]), True)
    pts = []
    for cx, cy, a0 in ((x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0), (x0 + r, y1 - r, 90), (x0 + r, y0 + r, 180)):
        arc = g.ellipse_arc_points(cx, cy, r, r, 0, math.radians(a0), math.radians(a0 + 90))
        pts.append(arc)
    return g.Polyline(np.vstack(pts), True)


def ellipse(w: float, h: float) -> g.Polyline:
    pts = g.ellipse_arc_points(0, 0, abs(w) / 2, abs(h) / 2, 0, 0, 2 * math.pi)[:-1]
    return g.Polyline(pts, True)


def polygon(n: int, w: float, h: float, star: float = 0.0, rotation: float = 0.0) -> g.Polyline:
    """Regelmäßiges n-Eck in die Box w × h eingepasst. star > 0: Stern mit Innenradius star·r."""
    n = max(3, int(n))
    k = 2 * n if star > 0 else n
    a = np.radians(rotation) + math.pi / 2 + np.arange(k) * 2 * math.pi / k
    r = np.ones(k)
    if star > 0:
        r[1::2] = max(0.05, min(star, 0.99))
    pts = np.column_stack([r * np.cos(a), r * np.sin(a)])
    bb = pts.min(axis=0), pts.max(axis=0)
    span = np.maximum(bb[1] - bb[0], 1e-9)
    pts = (pts - (bb[0] + bb[1]) / 2) / span * [abs(w), abs(h)]
    return g.Polyline(pts, True)


def polyline(points, closed=False) -> g.Polyline:
    return g.Polyline(np.asarray(points, float), closed)
