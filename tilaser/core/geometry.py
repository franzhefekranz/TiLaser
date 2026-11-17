"""Geometrie-Grundlagen: Polylinien, Transformationen, Kurven-Abtastung, Pfadoptimierung.

Alle Koordinaten in Millimetern, Y-Achse nach oben (Maschinenkoordinaten).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

# Standard-Toleranz für das Abtasten von Kurven (mm)
CURVE_TOL = 0.02


@dataclass
class Polyline:
    pts: np.ndarray            # (N, 2) float
    closed: bool = False

    def __post_init__(self):
        self.pts = np.asarray(self.pts, dtype=float).reshape(-1, 2)

    def copy(self) -> "Polyline":
        return Polyline(self.pts.copy(), self.closed)

    def length(self) -> float:
        p = self.loop_pts()
        if len(p) < 2:
            return 0.0
        return float(np.sum(np.hypot(*np.diff(p, axis=0).T)))

    def loop_pts(self) -> np.ndarray:
        """Punkte inklusive Rückkehr zum Startpunkt bei geschlossenen Pfaden."""
        if self.closed and len(self.pts) > 1 and not np.allclose(self.pts[0], self.pts[-1]):
            return np.vstack([self.pts, self.pts[:1]])
        return self.pts

    def transformed(self, m: np.ndarray) -> "Polyline":
        return Polyline(apply_matrix(m, self.pts), self.closed)


# --------------------------------------------------------------------------- Matrizen
def identity() -> np.ndarray:
    return np.eye(3)


def translate(tx: float, ty: float) -> np.ndarray:
    m = np.eye(3)
    m[0, 2], m[1, 2] = tx, ty
    return m


def scale(sx: float, sy: float | None = None) -> np.ndarray:
    if sy is None:
        sy = sx
    return np.diag([sx, sy, 1.0])


def rotate(deg: float) -> np.ndarray:
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])


def apply_matrix(m: np.ndarray, pts: np.ndarray) -> np.ndarray:
    pts = np.asarray(pts, dtype=float).reshape(-1, 2)
    return pts @ m[:2, :2].T + m[:2, 2]


def matrix_scale_factor(m: np.ndarray) -> float:
    """Mittlerer Skalierungsfaktor einer affinen Matrix (für Toleranzen)."""
    return math.sqrt(abs(np.linalg.det(m[:2, :2]))) or 1.0


# --------------------------------------------------------------------------- Kurven
def flatten_cubic(p0, p1, p2, p3, tol=CURVE_TOL) -> np.ndarray:
    """Kubische Bézierkurve -> Punkte (ohne p0). Segmentanzahl nach Wang-Formel."""
    p0, p1, p2, p3 = (np.asarray(p, float) for p in (p0, p1, p2, p3))
    d = max(np.hypot(*(p0 - 2 * p1 + p2)), np.hypot(*(p1 - 2 * p2 + p3)))
    n = int(min(500, max(2, math.ceil(math.sqrt(0.75 * d / tol)))))
    t = np.linspace(0, 1, n + 1)[1:, None]
    mt = 1 - t
    return mt ** 3 * p0 + 3 * mt ** 2 * t * p1 + 3 * mt * t ** 2 * p2 + t ** 3 * p3


def flatten_quad(p0, p1, p2, tol=CURVE_TOL) -> np.ndarray:
    p0, p1, p2 = (np.asarray(p, float) for p in (p0, p1, p2))
    d = np.hypot(*(p0 - 2 * p1 + p2))
    n = int(min(500, max(2, math.ceil(math.sqrt(0.25 * d / tol)))))
    t = np.linspace(0, 1, n + 1)[1:, None]
    mt = 1 - t
    return mt ** 2 * p0 + 2 * mt * t * p1 + t ** 2 * p2


def arc_segments(radius: float, sweep_rad: float, tol=CURVE_TOL) -> int:
    """Anzahl Segmente für einen Kreisbogen bei gegebener Sehnenabweichung."""
    radius = abs(radius)
    if radius < 1e-9:
        return 1
    x = max(-1.0, min(1.0, 1 - tol / radius))
    step = 2 * math.acos(x) if x < 1 else 0.1
    step = max(step, 1e-3)
    return int(min(2000, max(4, math.ceil(abs(sweep_rad) / step))))


def circle_points(cx, cy, r, tol=CURVE_TOL) -> np.ndarray:
    n = arc_segments(r, 2 * math.pi, tol)
    a = np.linspace(0, 2 * math.pi, n, endpoint=False)
    return np.column_stack([cx + r * np.cos(a), cy + r * np.sin(a)])


def ellipse_arc_points(cx, cy, rx, ry, rot_rad, a0, a1, tol=CURVE_TOL, include_start=True) -> np.ndarray:
    n = arc_segments(max(rx, ry), a1 - a0, tol)
    a = np.linspace(a0, a1, n + 1)
    if not include_start:
        a = a[1:]
    x, y = rx * np.cos(a), ry * np.sin(a)
    c, s = math.cos(rot_rad), math.sin(rot_rad)
    return np.column_stack([cx + c * x - s * y, cy + s * x + c * y])


def bulge_arc(p1, p2, bulge, tol=CURVE_TOL) -> np.ndarray:
    """DXF-Bulge-Bogen von p1 nach p2 (Punkte ohne p1, mit p2)."""
    p1, p2 = np.asarray(p1, float), np.asarray(p2, float)
    if abs(bulge) < 1e-9:
        return p2[None, :]
    c = p2 - p1
    d = math.hypot(*c)
    if d < 1e-12:
        return p2[None, :]
    theta = 4 * math.atan(bulge)
    n_left = np.array([-c[1], c[0]]) / d
    h = (d / 2) / math.tan(theta / 2)
    center = (p1 + p2) / 2 + n_left * h
    r = math.hypot(*(p1 - center))
    a0 = math.atan2(p1[1] - center[1], p1[0] - center[0])
    n = arc_segments(r, theta, tol)
    a = a0 + np.linspace(0, theta, n + 1)[1:]
    pts = np.column_stack([center[0] + r * np.cos(a), center[1] + r * np.sin(a)])
    pts[-1] = p2
    return pts


# --------------------------------------------------------------------------- Analyse
def bbox(polys) -> tuple[float, float, float, float] | None:
    arrs = [p.pts for p in polys if len(p.pts)]
    if not arrs:
        return None
    a = np.vstack(arrs)
    return float(a[:, 0].min()), float(a[:, 1].min()), float(a[:, 0].max()), float(a[:, 1].max())


def points_in_polygon(pts: np.ndarray, poly: np.ndarray) -> np.ndarray:
    """Even-Odd-Test (Strahlverfahren), vektorisiert über Polygonkanten."""
    pts = np.asarray(pts, float).reshape(-1, 2)
    x, y = pts[:, 0][:, None], pts[:, 1][:, None]
    a = poly
    b = np.roll(poly, -1, axis=0)
    xa, ya, xb, yb = a[:, 0], a[:, 1], b[:, 0], b[:, 1]
    cond = (ya > y) != (yb > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        xint = xa + (y - ya) * (xb - xa) / (yb - ya)
    hits = cond & (x < xint)
    return (np.sum(hits, axis=1) % 2) == 1


def nesting_depth(polys: list[Polyline]) -> list[int]:
    """Wie viele andere geschlossene Pfade umschließen jeden Pfad?"""
    n = len(polys)
    boxes = [(p.pts[:, 0].min(), p.pts[:, 1].min(), p.pts[:, 0].max(), p.pts[:, 1].max())
             if len(p.pts) else (0, 0, 0, 0) for p in polys]
    depth = [0] * n
    for i, p in enumerate(polys):
        if not len(p.pts):
            continue
        bi = boxes[i]
        probe = p.pts[len(p.pts) // 2]
        for j, q in enumerate(polys):
            if i == j or not q.closed or len(q.pts) < 3:
                continue
            bj = boxes[j]
            if bj[0] <= bi[0] and bj[1] <= bi[1] and bj[2] >= bi[2] and bj[3] >= bi[3] and bj != bi:
                if points_in_polygon(probe[None, :], q.pts)[0]:
                    depth[i] += 1
    return depth


def order_paths(polys: list[Polyline], start=(0.0, 0.0), inner_first=True) -> list[Polyline]:
    """Reihenfolge optimieren: innere Konturen zuerst, danach Nächster-Nachbar.

    Offene Pfade dürfen umgedreht werden, geschlossene beginnen am nächstgelegenen Punkt.
    """
    polys = [p for p in polys if len(p.pts) >= 2 or (len(p.pts) == 1)]
    if not polys:
        return []
    if inner_first:
        depth = nesting_depth(polys)
        groups: dict[int, list[Polyline]] = {}
        for d, p in zip(depth, polys):
            groups.setdefault(d, []).append(p)
        levels = sorted(groups, reverse=True)
    else:
        groups = {0: polys}
        levels = [0]

    out: list[Polyline] = []
    cur = np.asarray(start, float)
    for lv in levels:
        remaining = list(groups[lv])
        while remaining:
            best_i, best_d, best_mode = 0, float("inf"), ("start", 0)
            for i, p in enumerate(remaining):
                if p.closed:
                    d2 = np.sum((p.pts - cur) ** 2, axis=1)
                    k = int(np.argmin(d2))
                    if d2[k] < best_d:
                        best_i, best_d, best_mode = i, d2[k], ("rot", k)
                else:
                    ds = np.sum((p.pts[0] - cur) ** 2)
                    de = np.sum((p.pts[-1] - cur) ** 2)
                    if ds < best_d:
                        best_i, best_d, best_mode = i, ds, ("start", 0)
                    if de < best_d:
                        best_i, best_d, best_mode = i, de, ("rev", 0)
            p = remaining.pop(best_i)
            if best_mode[0] == "rot":
                k = best_mode[1]
                pts = p.pts
                if np.allclose(pts[0], pts[-1]) and len(pts) > 1:
                    pts = pts[:-1]
                pts = np.roll(pts, -k, axis=0)
                p = Polyline(pts, True)
            elif best_mode[0] == "rev":
                p = Polyline(p.pts[::-1].copy(), False)
            out.append(p)
            cur = p.loop_pts()[-1]
    return out


def simplify(pts: np.ndarray, tol=0.005) -> np.ndarray:
    """Entfernt doppelte und exakt kollineare Punkte (verlustarm)."""
    if len(pts) < 3:
        return pts
    keep = [0]
    for i in range(1, len(pts) - 1):
        a, b, c = pts[keep[-1]], pts[i], pts[i + 1]
        ab = b - a
        if np.hypot(*ab) < 1e-6:
            continue
        ac = c - a
        L = np.hypot(*ac)
        if L > 1e-9 and abs(ab[0] * ac[1] - ab[1] * ac[0]) / L < tol and np.dot(ab, ac) > 0 and np.dot(ab, ab) < np.dot(ac, ac):
            continue
        keep.append(i)
    keep.append(len(pts) - 1)
    return pts[keep]
