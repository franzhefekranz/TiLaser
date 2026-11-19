"""Flächenfüllung (Scanline-Schraffur) nach der Even-Odd-Regel."""
from __future__ import annotations

import math

import numpy as np

from . import geometry as g


def hatch(polys: list[g.Polyline], interval: float, angle_deg: float = 0.0, bidirectional: bool = True,
          groups: list[int] | None = None) -> list[np.ndarray]:
    """Gibt Brennsegmente zurück: Liste von (2,2)-Arrays [[x0,y0],[x1,y1]] in Fahrreihenfolge.

    Offene Pfade werden ignoriert (nur geschlossene Konturen begrenzen Flächen).
    Ohne ``groups`` gilt Even-Odd über alle Konturen: wo sich zwei Flächen überlappen, bleibt ein Loch.
    Mit ``groups`` (eine Gruppennummer je Pfad, z. B. das Objekt) gilt Even-Odd nur innerhalb einer Gruppe
    (Löcher eines Objekts bleiben Löcher); die Flächen verschiedener Gruppen werden vereinigt.
    """
    sel = [i for i, p in enumerate(polys) if p.closed and len(p.pts) >= 3]
    closed = [polys[i] for i in sel]
    if not closed or interval <= 0:
        return []
    rot = g.rotate(-angle_deg)
    back = g.rotate(angle_deg)
    edges_a, edges_b, edges_g = [], [], []
    for i, p in zip(sel, closed):
        pts = g.apply_matrix(rot, p.pts)
        edges_a.append(pts)
        edges_b.append(np.roll(pts, -1, axis=0))
        edges_g.append(np.full(len(pts), groups[i] if groups is not None else 0))
    A = np.vstack(edges_a)
    B = np.vstack(edges_b)
    G = np.concatenate(edges_g)
    keep = np.abs(A[:, 1] - B[:, 1]) > 1e-12      # horizontale Kanten ignorieren
    A, B, G = A[keep], B[keep], G[keep]
    multi = groups is not None and len(np.unique(G)) > 1
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
        xs_all = A[mask, 0] + (y - A[mask, 1]) * dx_dy[mask]
        if multi:
            pairs = _union_pairs(xs_all, G[mask])
        else:
            xs = np.sort(xs_all)
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


def _union_pairs(xs: np.ndarray, grp: np.ndarray) -> np.ndarray:
    """Even-Odd-Intervalle je Gruppe bilden und über alle Gruppen vereinigen."""
    iv = []
    for gid in np.unique(grp):
        x = np.sort(xs[grp == gid])
        iv.append(x[: len(x) // 2 * 2].reshape(-1, 2))
    allp = np.vstack(iv) if iv else np.zeros((0, 2))
    if not len(allp):
        return allp
    allp = allp[np.argsort(allp[:, 0])]
    out = [list(allp[0])]
    for a, b in allp[1:]:
        if a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return np.array(out)
