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
class BezPath:
    """Pfad aus Ankerpunkten mit Kurven-Anfassern (kubische Bézier-Segmente).

    Segment i führt von anchors[i] (Anfasser hout[i]) nach anchors[i+1] (Anfasser hin[i+1]).
    Liegen beide Anfasser auf ihren Ankern, ist das Segment eine Gerade.
    """
    anchors: np.ndarray
    hin: np.ndarray
    hout: np.ndarray
    closed: bool = False

    def __post_init__(self):
        self.anchors = np.asarray(self.anchors, float).reshape(-1, 2)
        self.hin = np.asarray(self.hin, float).reshape(-1, 2)
        self.hout = np.asarray(self.hout, float).reshape(-1, 2)

    @classmethod
    def from_points(cls, pts, closed=False) -> "BezPath":
        pts = np.asarray(pts, float).reshape(-1, 2)
        return cls(pts.copy(), pts.copy(), pts.copy(), closed)

    def copy(self) -> "BezPath":
        return BezPath(self.anchors.copy(), self.hin.copy(), self.hout.copy(), self.closed)

    def transformed(self, m) -> "BezPath":
        return BezPath(apply_matrix(m, self.anchors), apply_matrix(m, self.hin), apply_matrix(m, self.hout),
                       self.closed)

    def n_segments(self) -> int:
        n = len(self.anchors)
        return n if self.closed else max(0, n - 1)

    def segment(self, i):
        n = len(self.anchors)
        j = (i + 1) % n
        return self.anchors[i], self.hout[i], self.hin[j], self.anchors[j]

    def is_line(self, i) -> bool:
        p0, c1, c2, p1 = self.segment(i)
        return bool(np.allclose(c1, p0) and np.allclose(c2, p1))

    def flatten(self, tol=CURVE_TOL) -> np.ndarray:
        if len(self.anchors) == 0:
            return np.zeros((0, 2))
        out = [self.anchors[:1]]
        for i in range(self.n_segments()):
            p0, c1, c2, p1 = self.segment(i)
            if self.is_line(i):
                out.append(p1[None, :])
            else:
                out.append(flatten_cubic(p0, c1, c2, p1, tol))
        pts = np.vstack(out)
        if self.closed and len(pts) > 1:
            pts = pts[:-1]                          # Rückkehr zum Start nicht doppelt speichern
        return pts

    def split(self, i, t) -> None:
        """Segment i bei Parameter t teilen (de Casteljau), fügt einen Anker ein."""
        p0, c1, c2, p1 = self.segment(i)
        a, b, c = p0 + (c1 - p0) * t, c1 + (c2 - c1) * t, c2 + (p1 - c2) * t
        d, e = a + (b - a) * t, b + (c - b) * t
        f = d + (e - d) * t
        j = (i + 1) % len(self.anchors)
        line = self.is_line(i)
        self.hout[i] = p0 if line else a
        self.hin[j] = p1 if line else c
        k = i + 1
        self.anchors = np.insert(self.anchors, k, f, axis=0)
        self.hin = np.insert(self.hin, k, f if line else d, axis=0)
        self.hout = np.insert(self.hout, k, f if line else e, axis=0)

    def nearest(self, pt, samples=24):
        """(Segment, t, Abstand) des nächstgelegenen Kurvenpunkts."""
        best = (None, 0.0, float("inf"))
        t = np.linspace(0, 1, samples + 1)[:, None]
        for i in range(self.n_segments()):
            p0, c1, c2, p1 = self.segment(i)
            mt = 1 - t
            q = mt ** 3 * p0 + 3 * mt ** 2 * t * c1 + 3 * mt * t ** 2 * c2 + t ** 3 * p1
            d = np.hypot(q[:, 0] - pt[0], q[:, 1] - pt[1])
            k = int(np.argmin(d))
            if d[k] < best[2]:
                best = (i, float(t[k, 0]), float(d[k]))
        return best


@dataclass
class Polyline:
    pts: np.ndarray            # (N, 2) float
    closed: bool = False
    bez: BezPath | None = None # Kurvendarstellung (pts = bez.flatten()), falls vorhanden
    # Hilfsangaben für die Ausgabe (nicht Teil der Geometrie, werden beim Kopieren nicht übernommen):
    _order: int = field(default=0, init=False, repr=False, compare=False)        # Objektreihenfolge
    start_fixed: bool = field(default=False, init=False, repr=False, compare=False)  # Startpunkt festgelegt

    def __post_init__(self):
        self.pts = np.asarray(self.pts, dtype=float).reshape(-1, 2)

    @classmethod
    def from_bez(cls, bez: BezPath, tol=CURVE_TOL) -> "Polyline":
        return cls(bez.flatten(tol), bez.closed, bez)

    def copy(self) -> "Polyline":
        return Polyline(self.pts.copy(), self.closed, self.bez.copy() if self.bez is not None else None)

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
        if self.bez is not None:          # Kurven nach dem Transformieren neu abtasten (genau auch bei Vergrößerung)
            return Polyline.from_bez(self.bez.transformed(m))
        return Polyline(apply_matrix(m, self.pts), self.closed)


def ellipse_bez(cx, cy, rx, ry, rot=0.0) -> BezPath:
    """Ellipse aus 4 kubischen Segmenten (Abweichung < 0,03 % des Radius)."""
    k = 4 / 3 * (math.sqrt(2) - 1)
    a = np.array([[rx, 0], [0, ry], [-rx, 0], [0, -ry]], float)
    t = np.array([[0, ry], [-rx, 0], [0, -ry], [rx, 0]], float) * k
    c, s_ = math.cos(rot), math.sin(rot)
    R = np.array([[c, -s_], [s_, c]])
    anchors = a @ R.T + [cx, cy]
    tang = t @ R.T
    return BezPath(anchors, anchors - tang, anchors + tang, True)


def arc_to_cubics(cx, cy, rx, ry, phi, th1, dth):
    """Elliptischer Bogen -> Liste kubischer Segmente [(c1, c2, p1), ...]."""
    n = max(1, int(math.ceil(abs(dth) / (math.pi / 2) - 1e-9)))
    step = dth / n
    k = 4 / 3 * math.tan(step / 4)
    c, s_ = math.cos(phi), math.sin(phi)

    def E(t):
        return np.array([cx + rx * math.cos(t) * c - ry * math.sin(t) * s_,
                         cy + rx * math.cos(t) * s_ + ry * math.sin(t) * c])

    def dE(t):
        return np.array([-rx * math.sin(t) * c - ry * math.cos(t) * s_,
                         -rx * math.sin(t) * s_ + ry * math.cos(t) * c])

    segs = []
    for i in range(n):
        t0, t1 = th1 + i * step, th1 + (i + 1) * step
        segs.append((E(t0) + k * dE(t0), E(t1) - k * dE(t1), E(t1)))
    return segs


class BezBuilder:
    """Hilfsklasse für Importer: Pfade Stück für Stück als Bézier aufbauen."""

    def __init__(self, tol=CURVE_TOL):
        self.tol = tol
        self.out: list[Polyline] = []
        self.a, self.i, self.o = [], [], []

    def move(self, p):
        self.finish(False)
        p = np.asarray(p, float)
        self.a, self.i, self.o = [p], [p], [p]

    def line(self, p):
        if not self.a:
            self.move(p)
            return
        p = np.asarray(p, float)
        self.a.append(p)
        self.i.append(p)
        self.o.append(p)

    def cubic(self, c1, c2, p):
        if not self.a:
            return
        self.o[-1] = np.asarray(c1, float)
        p = np.asarray(p, float)
        self.a.append(p)
        self.i.append(np.asarray(c2, float))
        self.o.append(p)

    def quad(self, q, p):
        if not self.a:
            return
        p0, q, p = self.a[-1], np.asarray(q, float), np.asarray(p, float)
        self.cubic(p0 + (q - p0) * 2 / 3, p + (q - p) * 2 / 3, p)

    @property
    def current(self):
        return self.a[-1] if self.a else None

    def finish(self, closed=False):
        if len(self.a) >= 2:
            a, hi, ho = list(self.a), list(self.i), list(self.o)
            if closed and np.allclose(a[0], a[-1]):
                hi[0] = hi[-1]
                a, hi, ho = a[:-1], hi[:-1], ho[:-1]
            if len(a) >= 2:
                bez = BezPath(np.array(a), np.array(hi), np.array(ho), closed)
                self.out.append(Polyline.from_bez(bez, self.tol))
        self.a, self.i, self.o = [], [], []


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
def ortho_point(prev, pt):
    """Punkt waagrecht oder senkrecht zu prev ausrichten (die größere Richtung gewinnt)."""
    (px, py), (x, y) = prev, pt
    return (x, py) if abs(x - px) >= abs(y - py) else (px, y)


def bbox(polys) -> tuple[float, float, float, float] | None:
    arrs = [p.pts for p in polys if len(p.pts)]
    if not arrs:
        return None
    a = np.vstack(arrs)
    return float(a[:, 0].min()), float(a[:, 1].min()), float(a[:, 0].max()), float(a[:, 1].max())


def bbox_of(polys) -> tuple[float, float, float, float]:
    """Wie bbox(), aber für Fälle, in denen es Punkte geben muss (sonst ValueError statt None)."""
    bb = bbox(polys)
    if bb is None:
        raise ValueError("Keine Konturen vorhanden.")
    return bb


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
    boxes = np.array([(p.pts[:, 0].min(), p.pts[:, 1].min(), p.pts[:, 0].max(), p.pts[:, 1].max())
                      if len(p.pts) else (0, 0, 0, 0) for p in polys], float).reshape(-1, 4)
    container = np.array([p.closed and len(p.pts) >= 3 for p in polys], bool)
    depth = [0] * n
    for i, p in enumerate(polys):
        if not len(p.pts):
            continue
        bi = boxes[i]
        # nur Pfade, deren Box die eigene Box umfasst (vektorisiert vorgefiltert)
        cand = container & (boxes[:, 0] <= bi[0]) & (boxes[:, 1] <= bi[1]) & (boxes[:, 2] >= bi[2]) & \
            (boxes[:, 3] >= bi[3]) & ~np.all(boxes == bi, axis=1)
        cand[i] = False
        probe = p.pts[len(p.pts) // 2][None, :]
        for j in np.flatnonzero(cand):
            if points_in_polygon(probe, polys[j].pts)[0]:
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
        n = len(remaining)
        # Bounding-Boxen als untere Schranke: nur Kandidaten, deren Box näher liegt als der bisher beste
        # Treffer, werden genau geprüft. Ergebnis wie beim vollständigen Vergleich (gleiche Gleichstände),
        # aber ohne O(n²)-Python-Schleife.
        bb = np.array([[q.pts[:, 0].min(), q.pts[:, 1].min(), q.pts[:, 0].max(), q.pts[:, 1].max()]
                       for q in remaining]).reshape(-1, 4)
        alive = np.ones(n, bool)
        for _ in range(n):
            dx = np.maximum(np.maximum(bb[:, 0] - cur[0], cur[0] - bb[:, 2]), 0.0)
            dy = np.maximum(np.maximum(bb[:, 1] - cur[1], cur[1] - bb[:, 3]), 0.0)
            lb = np.where(alive, dx * dx + dy * dy, np.inf)
            k_try = min(n, 32)
            cand = np.argpartition(lb, k_try - 1)[:k_try] if k_try < n else np.arange(n)
            cand = cand[np.lexsort((cand, lb[cand]))]
            best_i, best_d, best_mode = -1, float("inf"), ("start", 0)

            def check(i):
                nonlocal best_i, best_d, best_mode
                p = remaining[i]
                fixed = getattr(p, "start_fixed", False)      # festgelegter Startpunkt: nicht drehen/umkehren
                if p.closed and not fixed:
                    d2 = np.sum((p.pts - cur) ** 2, axis=1)
                    k = int(np.argmin(d2))
                    if (d2[k], i) < (best_d, best_i if best_i >= 0 else n):
                        best_i, best_d, best_mode = i, float(d2[k]), ("rot", k)
                else:
                    ds = float(np.sum((p.pts[0] - cur) ** 2))
                    de = float(np.sum((p.pts[-1] - cur) ** 2))
                    if (ds, i) < (best_d, best_i if best_i >= 0 else n):
                        best_i, best_d, best_mode = i, ds, ("start", 0)
                    if not fixed and (de, i) < (best_d, best_i if best_i >= 0 else n) and not (best_i == i and de >= ds):
                        best_i, best_d, best_mode = i, de, ("rev", 0)

            done = False
            for i in cand:
                if lb[i] > best_d:
                    done = True
                    break
                check(int(i))
            if not done and k_try < n:                        # selten: die 32 nächsten Boxen reichten nicht
                rest = np.flatnonzero(alive & (lb <= best_d))
                for i in rest[np.lexsort((rest, lb[rest]))]:
                    check(int(i))
            alive[best_i] = False
            p = remaining[best_i]
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


def group_parts(polys: list[Polyline]) -> list[list[int]]:
    """Zerlegt Konturen in Teile: jede Außenkontur bleibt mit ihren Löchern zusammen
    (z. B. 'O', 'A' oder eine Platte mit Bohrungen). Offene Pfade sind eigene Teile.

    Rückgabe: Liste von Index-Listen, jeweils Außenkontur zuerst.
    """
    n = len(polys)
    if n == 0:
        return []
    depth = np.array(nesting_depth(polys))
    parent = list(range(n))
    container = np.array([p.closed and len(p.pts) >= 3 for p in polys], bool)
    boxes = np.array([(p.pts[:, 0].min(), p.pts[:, 1].min(), p.pts[:, 0].max(), p.pts[:, 1].max())
                      if len(p.pts) else (0, 0, 0, 0) for p in polys], float).reshape(-1, 4)
    areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    for i, p in enumerate(polys):
        if not container[i] or depth[i] % 2 == 0:
            continue
        probe = p.pts[len(p.pts) // 2][None, :]
        x, y = probe[0]
        # nur Außenkonturen eine Ebene darüber, deren Box den Probepunkt enthält – kleinste zuerst
        cand = container & (depth == depth[i] - 1) & (boxes[:, 0] <= x) & (boxes[:, 2] >= x) & \
            (boxes[:, 1] <= y) & (boxes[:, 3] >= y)
        cand[i] = False
        for j in sorted(np.flatnonzero(cand), key=lambda k: areas[k]):
            if points_in_polygon(probe, polys[j].pts)[0]:
                parent[i] = int(j)
                break
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(parent[i], []).append(i)
    out = []
    for root in sorted(groups):
        idx = groups[root]
        idx.sort(key=lambda k: k != root)
        out.append(idx)
    return out


def join_paths(polys: list[Polyline], tol: float = 0.05, close: bool = True) -> list[Polyline]:
    """Verbindet offene Pfade, deren Enden sich (bis tol) berühren, und schließt Pfade,
    deren Anfang und Ende zusammenfallen."""
    closed = [p for p in polys if p.closed]
    open_ = [p.pts.copy() for p in polys if not p.closed and len(p.pts) >= 2]
    merged = True
    while merged:
        merged = False
        for i in range(len(open_)):
            for j in range(len(open_)):
                if i == j:
                    continue
                a, b = open_[i], open_[j]
                if np.hypot(*(a[-1] - b[0])) <= tol:
                    new = np.vstack([a, b[1:]])
                elif np.hypot(*(a[-1] - b[-1])) <= tol:
                    new = np.vstack([a, b[::-1][1:]])
                elif np.hypot(*(a[0] - b[-1])) <= tol:
                    new = np.vstack([b, a[1:]])
                elif np.hypot(*(a[0] - b[0])) <= tol:
                    new = np.vstack([b[::-1], a[1:]])
                else:
                    continue
                open_[i] = new
                del open_[j]
                merged = True
                break
            if merged:
                break
    out = list(closed)
    for pts in open_:
        if close and len(pts) >= 3 and np.hypot(*(pts[0] - pts[-1])) <= tol:
            out.append(Polyline(pts[:-1], True))
        else:
            out.append(Polyline(pts, False))
    return out
