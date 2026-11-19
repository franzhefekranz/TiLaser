"""Kanten aneinanderlegen: ein Teil so drehen und verschieben, dass eine seiner geraden Kanten bündig an
einer Kante eines anderen Teils liegt (Außenseite an Außenseite, die Teile liegen danach nebeneinander).

Ablauf in der Oberfläche: erst die Kante des Teils anklicken, das bewegt wird, dann die Zielkante.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from . import geometry as g
from .clip import inside


@dataclass
class Edge:
    item_id: str
    a: np.ndarray            # Anfang (Welt)
    b: np.ndarray            # Ende (Welt)
    outward: np.ndarray      # Einheitsnormale, zeigt aus dem Material heraus

    @property
    def length(self) -> float:
        return float(np.hypot(*(self.b - self.a)))

    @property
    def mid(self) -> np.ndarray:
        return (self.a + self.b) / 2


def _seg_dist(p, a, b):
    d = b - a
    L2 = float(d @ d)
    t = 0.0 if L2 < 1e-18 else max(0.0, min(1.0, float((p - a) @ d) / L2))
    q = a + d * t
    return float(np.hypot(*(p - q)))


def _straight_run(pts: np.ndarray, closed: bool, k: int, angle_tol: float = 0.5):
    """Segment k nach beiden Seiten um fast gerade weiterlaufende Segmente verlängern (Kurven, die als
    viele kurze Stücke vorliegen, ergeben so keine sinnvolle Kante – gerade Kanten aber ganz)."""
    n = len(pts)
    nseg = n if closed else n - 1

    def seg(i):
        return pts[i % n], pts[(i + 1) % n]

    a, b = seg(k)
    d0 = (b - a) / max(1e-12, float(np.hypot(*(b - a))))
    tol = math.sin(math.radians(angle_tol))
    lo = hi = k
    for step in (-1, 1):
        i = k
        for _ in range(nseg - 1):
            j = i + step
            if not closed and (j < 0 or j >= nseg):
                break
            p, q = seg(j)
            d = q - p
            L = float(np.hypot(*d))
            if L < 1e-9 or abs(d0[0] * d[1] - d0[1] * d[0]) / L > tol or d @ d0 <= 0:
                break
            # muss auch auf derselben Geraden liegen
            if abs(float(d0[0] * (p - a)[1] - d0[1] * (p - a)[0])) > 1e-3:
                break
            i = j
        if step < 0:
            lo = i
        else:
            hi = i
    if closed and hi - lo + 1 >= nseg:
        return a, b
    return seg(lo)[0], seg(hi)[1]


def nearest_edge(candidates, x: float, y: float, max_dist: float):
    """candidates: Liste (item_id, Polylinien eines Teils in Weltkoordinaten).
    Liefert die nächste gerade Kante (Edge) innerhalb max_dist oder None."""
    p = np.array([x, y], float)
    best = None
    for item_id, polys in candidates:
        rings = [q for q in polys if q.closed and len(q.pts) >= 3]
        for poly in polys:
            pts = poly.pts
            if len(pts) < 2:
                continue
            nseg = len(pts) if poly.closed else len(pts) - 1
            for k in range(nseg):
                a, b = pts[k], pts[(k + 1) % len(pts)]
                d = _seg_dist(p, a, b)
                if d <= max_dist and (best is None or d < best[0]):
                    best = (d, item_id, poly, k, rings)
    if best is None:
        return None
    _d, item_id, poly, k, rings = best
    a, b = _straight_run(poly.pts, poly.closed, k)
    return Edge(item_id, np.array(a, float), np.array(b, float), outward_normal(rings or [poly], a, b))


def outward_normal(rings, a, b) -> np.ndarray:
    d = np.asarray(b, float) - np.asarray(a, float)
    L = max(1e-12, float(np.hypot(*d)))
    n = np.array([-d[1], d[0]]) / L
    mid = (np.asarray(a) + np.asarray(b)) / 2
    eps = min(0.05, 0.01 * L)
    # Material liegt innen: Normale, deren Probepunkt außerhalb liegt, zeigt nach außen
    return -n if inside((mid + n * eps)[None], rings)[0] else n


def align_matrix(moving: Edge, target: Edge, mode: str = "center", gap: float = 0.0) -> np.ndarray:
    """3×3-Matrix, die das bewegte Teil so dreht und verschiebt, dass seine Kante an der Zielkante liegt.

    Die Außenseite der bewegten Kante zeigt danach zur Zielkante hin (die Teile liegen nebeneinander, nicht
    übereinander). mode: 'center' (Mitten bündig), 'start' (links bzw. unten bündig) oder 'end' (rechts bzw.
    oben bündig). gap: Abstand zwischen den Kanten (0 = berührend)."""
    want = -target.outward
    a0 = math.atan2(moving.outward[1], moving.outward[0])
    a1 = math.atan2(want[1], want[0])
    deg = math.degrees(a1 - a0)
    rot = g.translate(*moving.mid) @ g.rotate(deg) @ g.translate(*(-moving.mid))
    ma, mb = g.apply_matrix(rot, np.array([moving.a, moving.b]))
    u = (target.b - target.a) / max(1e-12, target.length)
    ts = sorted(((ma - target.a) @ u, (mb - target.a) @ u))
    # „Anfang“ = links bzw. unten auf dem Bildschirm, unabhängig von der Laufrichtung der Kontur
    backwards = (u[0] < 0) if abs(u[0]) >= abs(u[1]) else (u[1] < 0)
    if backwards and mode in ("start", "end"):
        mode = "end" if mode == "start" else "start"
    if mode == "start":
        shift_t = 0.0 - ts[0]
    elif mode == "end":
        shift_t = target.length - ts[1]
    else:
        shift_t = target.length / 2 - (ts[0] + ts[1]) / 2
    # quer zur Kante: auf die Zielgerade (+ Abstand nach außen)
    off = float((ma - target.a) @ target.outward)
    move = u * shift_t + target.outward * (gap - off)
    return g.translate(*move) @ rot


def rotation_deg(m: np.ndarray) -> float:
    return math.degrees(math.atan2(m[1, 0], m[0, 0]))
