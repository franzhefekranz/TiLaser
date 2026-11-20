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


def bend_length(radius: float, angle_deg: float, thickness: float) -> float:
    """Länge der Biegezone: Bogen auf der neutralen Faser (Innenradius + halbe Materialstärke)."""
    return math.radians(angle_deg) * (radius + thickness / 2)


def strip_between(target: Edge, ma: np.ndarray, mb: np.ndarray, length: float, extend: float = 0.0,
                  inset: float = 0.0):
    """Rechteck zwischen der Zielkante und der (bereits verschobenen) Gegenkante im Abstand length.

    Quer zur Kante reicht es von -extend bis length + extend; entlang der Kante über den Bereich, in dem sich
    beide Kanten überdecken (an beiden Enden um inset eingerückt). Rückgabe: (Ring, Breite) oder (None, 0)."""
    u = (target.b - target.a) / max(1e-12, target.length)
    s = sorted((float((ma - target.a) @ u), float((mb - target.a) @ u)))
    o0, o1 = max(0.0, s[0]) + inset, min(target.length, s[1]) - inset
    if o1 - o0 <= 1e-6:
        return None, 0.0
    n = target.outward
    ring = np.array([target.a + u * o0 - n * extend, target.a + u * o1 - n * extend,
                     target.a + u * o1 + n * (length + extend), target.a + u * o0 + n * (length + extend)])
    return ring, o1 - o0


def tidy_ring(pts: np.ndarray, eps: float = 0.02, angle_deg: float = 0.2) -> np.ndarray:
    """Winzige Stufen (< eps) und fast gerade Knicke aus einem geschlossenen Ring entfernen
    (entstehen beim Vereinigen von Flächen, deren Kanten bewusst minimal versetzt sind)."""
    p = [np.asarray(q, float) for q in pts]
    changed = True
    while changed and len(p) > 3:
        changed = False
        for i in range(len(p)):
            a, b = p[i], p[(i + 1) % len(p)]
            if np.hypot(*(b - a)) < eps:
                del p[(i + 1) % len(p)]
                changed = True
                break
        else:
            for i in range(len(p)):
                a, b, c = p[i - 1], p[i], p[(i + 1) % len(p)]
                u, v = b - a, c - b
                nu, nv = np.hypot(*u), np.hypot(*v)
                if nu < 1e-12 or nv < 1e-12:
                    continue
                cross = (u[0] * v[1] - u[1] * v[0]) / (nu * nv)
                if abs(cross) < math.sin(math.radians(angle_deg)) and u @ v > 0:
                    del p[i]
                    changed = True
                    break
    return np.array(p)


# ---------------------------------------------------------------- An Knoten fangen

def path_nodes(polys) -> np.ndarray:
    """Knoten (Eckpunkte) von Pfaden: bei Kurven die Ankerpunkte, sonst die Punkte des Linienzugs.
    Bei geschlossenen Pfaden ohne doppelten Endpunkt."""
    out = []
    for p in polys:
        pts = p.bez.anchors if getattr(p, "bez", None) is not None else p.pts
        pts = np.asarray(pts, float).reshape(-1, 2)
        if len(pts) > 1 and p.closed and np.hypot(*(pts[0] - pts[-1])) < 1e-9:
            pts = pts[:-1]
        out.append(pts)
    return np.vstack(out) if out else np.zeros((0, 2))


def nearest_node(nodes: np.ndarray, x: float, y: float, tol: float):
    """Nächster Knoten zu (x, y) innerhalb tol oder None."""
    if not len(nodes):
        return None
    d = np.hypot(nodes[:, 0] - x, nodes[:, 1] - y)
    i = int(np.argmin(d))
    return nodes[i].copy() if d[i] <= tol else None


def node_snap_delta(ref, delta, targets: np.ndarray, tol: float):
    """Beim Ziehen: Der gegriffene Knoten ref wird um delta verschoben. Liegt er dann näher als tol an einem
    Zielknoten, wird delta so korrigiert, dass er genau darauf liegt. Rückgabe (delta, Ziel) oder None."""
    p = np.asarray(ref, float) + np.asarray(delta, float)
    t = nearest_node(targets, float(p[0]), float(p[1]), tol)
    if t is None:
        return None
    return t - np.asarray(ref, float), t
