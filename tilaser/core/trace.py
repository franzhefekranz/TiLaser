"""Bild nachzeichnen (Trace): Rasterbild -> geschlossene Vektorkonturen.

Verfahren: Schwellwert -> Pixelkanten zwischen Vorder- und Hintergrund zu Schleifen
verketten -> Douglas-Peucker-Vereinfachung. Ohne externe Bibliotheken.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from . import geometry as g
from .raster import adjust


def _rdp(pts: np.ndarray, tol: float) -> np.ndarray:
    """Douglas-Peucker für offene Linienzüge (iterativ)."""
    n = len(pts)
    if n < 3:
        return pts
    keep = np.zeros(n, bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        seg = pts[b] - pts[a]
        L = np.hypot(*seg)
        sub = pts[a + 1:b] - pts[a]
        if L < 1e-12:
            d = np.hypot(sub[:, 0], sub[:, 1])
        else:
            d = np.abs(sub[:, 0] * seg[1] - sub[:, 1] * seg[0]) / L
        i = int(np.argmax(d))
        if d[i] > tol:
            k = a + 1 + i
            keep[k] = True
            stack.append((a, k))
            stack.append((k, b))
    return pts[keep]


def _rdp_closed(pts: np.ndarray, tol: float) -> np.ndarray:
    if len(pts) < 4:
        return pts
    far = int(np.argmax(np.sum((pts - pts[0]) ** 2, axis=1)))
    a = _rdp(pts[: far + 1], tol)
    b = _rdp(np.vstack([pts[far:], pts[:1]]), tol)
    return np.vstack([a[:-1], b[:-1]])


def _boundary_edges(mask: np.ndarray) -> np.ndarray:
    """Gerichtete Pixelkanten (x0, y0, x1, y1); Vordergrund liegt in Laufrichtung rechts."""
    m = np.pad(mask, 1, constant_values=False)
    edges = []
    # senkrechte Kanten an Gitterlinie x zwischen Pixel (y, x-1) und (y, x)
    left, right = m[1:-1, :-1], m[1:-1, 1:]
    ys, xs = np.nonzero(left & ~right)                     # links Vordergrund -> nach unten
    edges.append(np.column_stack([xs, ys, xs, ys + 1]))
    ys, xs = np.nonzero(~left & right)                     # rechts Vordergrund -> nach oben
    edges.append(np.column_stack([xs, ys + 1, xs, ys]))
    # waagrechte Kanten an Gitterlinie y zwischen Pixel (y-1, x) und (y, x)
    top, bot = m[:-1, 1:-1], m[1:, 1:-1]
    ys, xs = np.nonzero(top & ~bot)                        # oben Vordergrund -> nach links
    edges.append(np.column_stack([xs + 1, ys, xs, ys]))
    ys, xs = np.nonzero(~top & bot)                        # unten Vordergrund -> nach rechts
    edges.append(np.column_stack([xs, ys, xs + 1, ys]))
    return np.vstack(edges).astype(np.int64)                # Indizes entsprechen bereits Gitterkoordinaten


def _link(edges: np.ndarray) -> list[np.ndarray]:
    """Verkettet gerichtete Kanten zu geschlossenen Schleifen."""
    W = int(edges[:, [0, 2]].max()) + 3 if len(edges) else 1
    key = lambda x, y: (y + 1) * W + (x + 1)
    starts: dict[int, list[int]] = {}
    for i, (x0, y0, _, _) in enumerate(edges.tolist()):
        starts.setdefault(key(x0, y0), []).append(i)
    used = np.zeros(len(edges), bool)
    loops = []
    E = edges.tolist()
    for i0 in range(len(E)):
        if used[i0]:
            continue
        loop = []
        i = i0
        while True:
            used[i] = True
            x0, y0, x1, y1 = E[i]
            loop.append((x0, y0))
            cand = [j for j in starts.get(key(x1, y1), []) if not used[j]]
            if not cand:
                break
            if len(cand) > 1:                 # Sattelpunkt: rechts abbiegen -> diagonale Pixel getrennt
                dx, dy = x1 - x0, y1 - y0
                def turn(j):
                    ex, ey = E[j][2] - E[j][0], E[j][3] - E[j][1]
                    return -(dx * ey - dy * ex)
                cand.sort(key=turn)
            i = cand[0]
        if len(loop) >= 4:
            loops.append(np.array(loop, float))
    return loops


def trace_image(gray: np.ndarray, w_mm: float, h_mm: float, threshold: int = 128, invert: bool = False,
                smooth: float = 0.8, min_area_px: float = 4.0, max_px: int = 1200,
                brightness: float = 0.0, contrast: float = 0.0) -> list[g.Polyline]:
    """Gibt geschlossene Konturen in lokalen mm-Koordinaten zurück (Mitte = 0/0, Y nach oben).

    smooth   = Vereinfachungstoleranz in Pixeln
    min_area = kleinere Flecken werden verworfen (Pixel²)
    """
    h, w = gray.shape
    k = min(1.0, max_px / max(h, w))
    img = gray
    if k < 1.0:
        img = np.asarray(Image.fromarray(gray).resize((max(1, int(w * k)), max(1, int(h * k))),
                                                      Image.Resampling.LANCZOS))
    v = adjust(img, invert, brightness, contrast)
    mask = v < threshold / 255.0
    if not mask.any():
        return []
    H, W = mask.shape
    sx, sy = w_mm / W, h_mm / H
    out = []
    for loop in _link(_boundary_edges(mask)):
        area = 0.5 * abs(np.sum(loop[:, 0] * np.roll(loop[:, 1], -1) - np.roll(loop[:, 0], -1) * loop[:, 1]))
        if area < min_area_px:
            continue
        simp = _rdp_closed(loop, smooth) if smooth > 0 else loop
        if len(simp) < 3:
            continue
        pts = np.column_stack([(simp[:, 0] - W / 2) * sx, (H / 2 - simp[:, 1]) * sy])
        out.append(g.Polyline(pts, True))
    return out
