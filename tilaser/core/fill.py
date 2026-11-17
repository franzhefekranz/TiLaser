"""Flächenfüllung (Scanline-Schraffur) nach der Even-Odd-Regel."""
from __future__ import annotations

import math

import numpy as np

from . import geometry as g


def hatch(polys: list[g.Polyline], interval: float, angle_deg: float = 0.0, bidirectional: bool = True
          ) -> list[np.ndarray]:
    """Gibt Brennsegmente zurück: Liste von (2,2)-Arrays [[x0,y0],[x1,y1]] in Fahrreihenfolge.

    Offene Pfade werden ignoriert (nur geschlossene Konturen begrenzen Flächen).
    """
    closed = [p for p in polys if p.closed and len(p.pts) >= 3]
    if not closed or interval <= 0:
        return []
    rot = g.rotate(-angle_deg)
    back = g.rotate(angle_deg)
    edges_a, edges_b = [], []
    for p in closed:
        pts = g.apply_matrix(rot, p.pts)
        edges_a.append(pts)
        edges_b.append(np.roll(pts, -1, axis=0))
    A = np.vstack(edges_a)
    B = np.vstack(edges_b)
    keep = np.abs(A[:, 1] - B[:, 1]) > 1e-12      # horizontale Kanten ignorieren
    A, B = A[keep], B[keep]
    if not len(A):
        return []
    ymin = min(A[:, 1].min(), B[:, 1].min())
    ymax = max(A[:, 1].max(), B[:, 1].max())
    n = int(math.floor((ymax - ymin) / interval))
    ys = ymin + interval / 2 + np.arange(n + 1) * interval
    ys = ys[ys < ymax]
    lo = np.minimum(A[:, 1], B[:, 1])
    hi = np.maximum(A[:, 1], B[:, 1])
    dx_dy = (B[:, 0] - A[:, 0]) / (B[:, 1] - A[:, 1])
    segs: list[np.ndarray] = []
    forward = True
    for y in ys:
        mask = (lo <= y) & (hi > y)
        if not mask.any():
            continue
        xs = np.sort(A[mask, 0] + (y - A[mask, 1]) * dx_dy[mask])
        pairs = xs[: len(xs) // 2 * 2].reshape(-1, 2)
        pairs = pairs[(pairs[:, 1] - pairs[:, 0]) > 1e-6]
        if not len(pairs):
            continue
        row = [np.array([[a, y], [b, y]]) for a, b in pairs]
        if bidirectional and not forward:
            row = [s[::-1] for s in row[::-1]]
        for s in row:
            segs.append(g.apply_matrix(back, s))
        forward = not forward
    return segs
