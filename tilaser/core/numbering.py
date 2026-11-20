"""Teile nummerieren: gleiche Teile erkennen (auch gedreht, wahlweise gespiegelt) und eine Nummer an die
Stelle gravieren, an der am meisten Platz ist."""
from __future__ import annotations

import math

import numpy as np

from . import geometry as g
from .clip import distance_to_edges, inside


# ---------------------------------------------------------------- Kennwerte

def _signed_area(r: np.ndarray) -> float:
    return 0.5 * float(np.sum(r[:, 0] * np.roll(r[:, 1], -1) - np.roll(r[:, 0], -1) * r[:, 1]))


def _perimeter(r: np.ndarray) -> float:
    return float(np.sum(np.hypot(*(np.roll(r, -1, axis=0) - r).T)))


def _material(rings: list[np.ndarray]):
    """Fläche (Even-Odd), Schwerpunkt und Trägheitsmatrix der Teilfläche."""
    polys = [g.Polyline(r, True) for r in rings]
    depth = g.nesting_depth(polys)
    A, cx, cy, ixx, iyy, ixy = 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    for r, dep in zip(rings, depth):
        sgn = 1.0 if dep % 2 == 0 else -1.0
        a = _signed_area(r)
        if a < 0:
            r = r[::-1]
            a = -a
        x, y = r[:, 0], r[:, 1]
        x1, y1 = np.roll(x, -1), np.roll(y, -1)
        cr = x * y1 - x1 * y
        A += sgn * a
        cx += sgn * float(np.sum((x + x1) * cr)) / 6
        cy += sgn * float(np.sum((y + y1) * cr)) / 6
        ixx += sgn * float(np.sum((y * y + y * y1 + y1 * y1) * cr)) / 12
        iyy += sgn * float(np.sum((x * x + x * x1 + x1 * x1) * cr)) / 12
        ixy += sgn * float(np.sum((x * y1 + 2 * x * y + 2 * x1 * y1 + x1 * y) * cr)) / 24
    if abs(A) < 1e-12:
        c = np.vstack(rings).mean(axis=0)
        return 0.0, c, np.eye(2)
    c = np.array([cx / A, cy / A])
    # Trägheit um den Schwerpunkt
    sxx = iyy / A - c[0] ** 2
    syy = ixx / A - c[1] ** 2
    sxy = ixy / A - c[0] * c[1]
    return A, c, np.array([[sxx, sxy], [sxy, syy]])


class PartShape:
    """Normierte Form eines Teils für den Vergleich."""

    def __init__(self, rings: list[np.ndarray], samples: int = 160):
        self.rings = [np.asarray(r, float) for r in rings if len(r) >= 3]
        self.area, self.center, cov = _material(self.rings)
        self.perimeter = sum(_perimeter(r) for r in self.rings)
        self.n_rings = len(self.rings)
        ev, vec = np.linalg.eigh(cov)
        self.axis = math.degrees(math.atan2(vec[1, 1], vec[0, 1]))    # Hauptachse (größere Ausdehnung)
        self.round = abs(ev[1] - ev[0]) < 1e-3 * max(abs(ev[1]), 1e-9)  # keine eindeutige Hauptachse
        self.local = [r - self.center for r in self.rings]
        self.samples = self._sample(samples)

    def _sample(self, n: int) -> np.ndarray:
        """Gleichmäßig verteilte Punkte auf allen Kanten (relativ zum Schwerpunkt)."""
        pts = []
        total = max(self.perimeter, 1e-9)
        for r in self.local:
            loop = np.vstack([r, r[:1]])
            seg = np.hypot(*np.diff(loop, axis=0).T)
            k = max(4, int(round(n * float(seg.sum()) / total)))
            t = np.linspace(0, seg.sum(), k, endpoint=False)
            cum = np.concatenate([[0], np.cumsum(seg)])
            idx = np.clip(np.searchsorted(cum, t, side="right") - 1, 0, len(seg) - 1)
            f = (t - cum[idx]) / np.maximum(seg[idx], 1e-12)
            pts.append(loop[idx] + (loop[idx + 1] - loop[idx]) * f[:, None])
        return np.vstack(pts)


def same_part(a: PartShape, b: PartShape, tol: float = 0.2, mirror: bool = False) -> bool:
    """Sind a und b dasselbe Teil (verschoben, gedreht, bei mirror auch gespiegelt)?"""
    if a.n_rings != b.n_rings:
        return False
    if abs(a.area - b.area) > 0.01 * max(abs(a.area), 1.0) + tol * a.perimeter * 0.5:
        return False
    if abs(a.perimeter - b.perimeter) > 0.01 * a.perimeter + 2 * tol:
        return False
    b_rings = b.local
    flips = [False, True] if mirror else [False]
    for flip in flips:
        pts = a.samples * ([-1.0, 1.0] if flip else [1.0, 1.0])
        axis_a = (180.0 - a.axis) if flip else a.axis
        if a.round or b.round:
            angles = list(np.arange(0.0, 360.0, 1.0))
        else:
            d = b.axis - axis_a
            angles = [d, d + 180.0]
        for ang in angles:
            q = g.apply_matrix(g.rotate(ang), pts)
            if float(distance_to_edges(q, b_rings).max()) <= tol:
                return True
    return False


def group_parts(parts: list[list[np.ndarray]], tol: float = 0.2, mirror: bool = False) -> list[int]:
    """Gruppennummer (0, 1, 2 …) je Teil; gleiche Teile bekommen dieselbe Gruppe.

    Die Gruppen sind nach dem ersten Vorkommen in der übergebenen Reihenfolge nummeriert."""
    shapes = [PartShape(p) for p in parts]
    reps: list[PartShape] = []
    out = []
    for s in shapes:
        for k, r in enumerate(reps):
            if same_part(s, r, tol, mirror):
                out.append(k)
                break
        else:
            reps.append(s)
            out.append(len(reps) - 1)
    return out


def reading_order(parts: list[list[np.ndarray]], row_tol: float = 0.0) -> list[int]:
    """Reihenfolge wie beim Lesen: Zeilen von oben nach unten, darin von links nach rechts."""
    boxes = [g.bbox_of([g.Polyline(r, True) for r in p]) for p in parts]
    if not boxes:
        return []
    tol = row_tol or 0.5 * float(np.median([b[3] - b[1] for b in boxes]))
    order = sorted(range(len(parts)), key=lambda i: -boxes[i][3])
    rows: list[list[int]] = []
    for i in order:
        if rows and abs(boxes[rows[-1][0]][3] - boxes[i][3]) <= tol:
            rows[-1].append(i)
        else:
            rows.append([i])
    return [i for row in rows for i in sorted(row, key=lambda j: boxes[j][0])]


# ---------------------------------------------------------------- Platz für die Nummer

def label_spot(rings: list[np.ndarray], steps: int = 24) -> tuple[np.ndarray, float]:
    """Punkt im Material mit dem größten Abstand zu allen Kanten (und dieser Abstand)."""
    polys = [g.Polyline(r, True) for r in rings]
    x0, y0, x1, y1 = g.bbox_of(polys)
    centroid = _material([np.asarray(r, float) for r in rings])[1]
    best, best_d = None, -1.0
    cx, cy, hw, hh = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
    for _ in range(4):                                     # grob, dann immer feiner um den besten Punkt
        xs = np.linspace(cx - hw, cx + hw, steps)
        ys = np.linspace(cy - hh, cy + hh, steps)
        grid = np.array([(x, y) for y in ys for x in xs])
        ok = inside(grid, rings)
        if ok.any():
            cand = grid[ok]
            d = distance_to_edges(cand, rings)
            # unter fast gleich guten Stellen die nächste zum Schwerpunkt (Nummer sitzt mittig, z. B. im Rechteck)
            good = np.flatnonzero(d >= 0.97 * d.max())
            k = int(good[np.argmin(np.hypot(*(cand[good] - centroid).T))])
            if d[k] > best_d * 0.97:
                best, best_d = cand[k], float(d[k])
        if best is None:
            break
        cx, cy = best
        hw, hh = hw / 4, hh / 4
    if best is None:
        c = np.vstack(rings).mean(axis=0)
        return c, 0.0
    return best, best_d


def number_paths(text: str, rings: list[np.ndarray], font: str, max_height: float = 5.0,
                 min_height: float = 1.5, margin: float = 0.8) -> tuple[list[g.Polyline], float]:
    """Konturen der Nummer, zentriert an der freiesten Stelle des Teils.

    Die Schrift wird so verkleinert, dass sie mit dem Randabstand vollständig ins Material passt.
    Rückgabe: (Konturen, tatsächliche Höhe der Ziffern); leer, wenn nicht einmal min_height passt."""
    from .text_import import text_to_polylines
    spot, room = label_spot(rings)
    base = text_to_polylines(text, font, 10.0)
    if not base or room <= margin:
        return [], 0.0
    x0, y0, x1, y1 = g.bbox_of(base)
    w, h = x1 - x0, y1 - y0
    k = min(max_height / h, (room - margin) / (0.5 * math.hypot(w, h)))   # Textkasten im freien Kreis
    if k * h < min_height:
        return [], 0.0
    m = g.translate(*spot) @ g.scale(k) @ g.translate(-(x0 + x1) / 2, -(y0 + y1) / 2)
    return [p.transformed(m) for p in base], k * h
