"""Fingerzinken an den Kanten zweier Teile, optional mit Dogbone-Ecken.

Die beiden Teile werden so hingelegt, dass sich die zu verbindenden Kanten berühren (auf einer
Linie liegen und sich überlappen). Auf dem gemeinsamen Stück werden abwechselnd Zinken und
Aussparungen erzeugt – beim zweiten Teil genau umgekehrt.

Arten:
* ``tabs``   – ineinandergreifend (Standard): Teil 1 bekommt Zinken, die um die Materialstärke
  herausragen, Teil 2 genau passende Aussparungen. Liegen die Teile wie gezeichnet, füllen die
  Zinken die Aussparungen lückenlos – das eine Teil ist mit dem anderen verzinkt.
* ``corner`` – Eckverbindung wie beim Box-Generator: beide Teile behalten ihr Außenmaß und bekommen
  abwechselnd Aussparungen (versetzt); erst um 90° gedreht greifen sie ineinander.

Dogbones: An jeder Innenecke, die durch die Zinken entsteht, wird ein Kreisbogen mit dem
angegebenen Durchmesser freigeschnitten (Fräser- oder Kerf-Ausgleich für eckige Zinken).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from . import geometry as g
from .clip import inside


@dataclass
class SharedEdge:
    ra: int          # Ring in Teil A
    ka: int          # Segment (Punkt k -> k+1)
    rb: int
    kb: int
    p0: np.ndarray   # Anfang des gemeinsamen Stücks
    p1: np.ndarray   # Ende
    length: float


def _segments(ring: np.ndarray):
    a = ring
    b = np.roll(ring, -1, axis=0)
    return a, b


def find_shared_edges(rings_a, rings_b, tol: float = 0.5, min_len: float = 3.0,
                      angle_tol_deg: float = 1.0) -> list[SharedEdge]:
    """Alle geraden Kantenstücke von A und B, die (bis tol) auf einer Linie liegen und sich überlappen."""
    out = []
    sin_tol = math.sin(math.radians(angle_tol_deg))
    for ia, ra in enumerate(rings_a):
        a0, a1 = _segments(ra)
        for ib, rb in enumerate(rings_b):
            b0, b1 = _segments(rb)
            for ka in range(len(a0)):
                d = a1[ka] - a0[ka]
                la = float(np.hypot(*d))
                if la < min_len:
                    continue
                u = d / la
                n = np.array([-u[1], u[0]])
                db = b1 - b0
                lb = np.hypot(db[:, 0], db[:, 1])
                ok = lb >= min_len
                cross = np.abs(u[0] * db[:, 1] - u[1] * db[:, 0]) / np.maximum(lb, 1e-12)
                ok &= cross <= sin_tol
                dist0 = np.abs((b0 - a0[ka]) @ n)
                dist1 = np.abs((b1 - a0[ka]) @ n)
                ok &= (dist0 <= tol) & (dist1 <= tol)
                for kb in np.nonzero(ok)[0]:
                    s0, s1 = sorted(((b0[kb] - a0[ka]) @ u, (b1[kb] - a0[ka]) @ u))
                    lo, hi = max(0.0, s0), min(la, s1)
                    if hi - lo >= min_len:
                        out.append(SharedEdge(ia, ka, ib, int(kb), a0[ka] + u * lo, a0[ka] + u * hi, hi - lo))
    out.sort(key=lambda e: -e.length)
    return out


def finger_count(length: float, finger: float) -> int:
    n = max(1, int(round(length / max(finger, 1e-6))))
    return n if n % 2 == 1 else n + 1 if length / (n + 1) >= finger * 0.6 else max(1, n - 1)


def _material_normal(rings, p0, p1) -> np.ndarray:
    """Einheitsnormale der Kante, die ins Material zeigt."""
    u = (p1 - p0) / max(1e-12, float(np.hypot(*(p1 - p0))))
    n = np.array([-u[1], u[0]])
    mid = (p0 + p1) / 2
    eps = min(0.05, 0.01 * float(np.hypot(*(p1 - p0))))
    return n if inside((mid + n * eps)[None], rings)[0] else -n


def _apply_profile(ring: np.ndarray, k: int, p0, p1, depths, n_in) -> np.ndarray:
    """Segment k des Rings: zwischen p0 und p1 durch das Zinkenprofil ersetzen.
    depths: Liste (Tiefe) je Abschnitt; Tiefe > 0 geht ins Material, < 0 heraus."""
    a, b = ring[k], ring[(k + 1) % len(ring)]
    u = (b - a) / max(1e-12, float(np.hypot(*(b - a))))
    if (p1 - p0) @ u < 0:
        p0, p1 = p1, p0
        depths = depths[::-1]
    L = float(np.hypot(*(p1 - p0)))
    w = L / len(depths)
    pts = [p0]
    for i, d in enumerate(depths):
        s0, s1 = i * w, (i + 1) * w
        pts += [p0 + u * s0 + n_in * d, p0 + u * s1 + n_in * d]
    pts.append(p1)
    new = [ring[: k + 1]] + [np.array(pts)] + [ring[k + 1:]]
    out = np.vstack([q for q in new if len(q)])
    keep = [0] + [i for i in range(1, len(out)) if np.hypot(*(out[i] - out[i - 1])) > 1e-9]
    out = out[keep]
    if len(out) > 1 and np.hypot(*(out[0] - out[-1])) <= 1e-9:
        out = out[:-1]
    return _cleanup(out)


def _cleanup(ring: np.ndarray) -> np.ndarray:
    """Doppelte Punkte, Punkte auf einer Geraden und Rückwärts-Spitzen entfernen."""
    pts = [p for p in ring]
    changed = True
    while changed and len(pts) > 3:
        changed = False
        for i in range(len(pts)):
            a, v, b = pts[i - 1], pts[i], pts[(i + 1) % len(pts)]
            d1, d2 = v - a, b - v
            if np.hypot(*d1) < 1e-9 or np.hypot(*d2) < 1e-9 or abs(d1[0] * d2[1] - d1[1] * d2[0]) < 1e-9:
                pts.pop(i)
                changed = True
                break
    return np.array(pts)


def add_dogbones(rings: list[np.ndarray], diameter: float, only_near=None, near_tol: float = 1e-6) -> list[np.ndarray]:
    """Kreisförmige Freischnitte an Innenecken (rechtwinklig o. ä.). only_near: Liste von Punkten –
    nur Ecken bearbeiten, die (bis near_tol) mit einem dieser Punkte übereinstimmen."""
    if diameter <= 0:
        return rings
    r = diameter / 2
    out = []
    near = np.asarray(only_near) if only_near is not None and len(only_near) else None
    for ring in rings:
        n = len(ring)
        prev = np.roll(ring, 1, axis=0)
        nxt = np.roll(ring, -1, axis=0)
        new = []
        for i in range(n):
            v = ring[i]
            if near is not None and np.min(np.hypot(*(near - v).T)) > near_tol:
                new.append(v)
                continue
            la, lb = np.hypot(*(v - prev[i])), np.hypot(*(nxt[i] - v))
            if la < 1e-9 or lb < 1e-9:
                new.append(v)
                continue
            a = (v - prev[i]) / la
            b = (nxt[i] - v) / lb
            turn = b - a
            tl = float(np.hypot(*turn))
            if tl < 1e-6:                     # gerade weiter
                new.append(v)
                continue
            bis = turn / tl
            eps = 1e-3 * min(1.0, la, lb)
            if inside((v + bis * eps)[None], rings)[0]:     # Außenecke: Material auf der Innenseite der Kurve
                new.append(v)
                continue
            cosang = float(np.clip(-(a @ b), -1, 1))
            half = math.acos(cosang) / 2                 # halber Innenwinkel
            if half < math.radians(20) or half > math.radians(160):
                new.append(v)
                continue
            c = v + bis * r                               # Kreis durch die Ecke
            chord = 2 * r * math.cos(half)               # Abstand Ecke -> zweiter Schnittpunkt je Kante
            if chord > la - 1e-6 or chord > lb - 1e-6:
                new.append(v)
                continue
            q1 = v - a * chord
            q2 = v + b * chord
            new.append(q1)
            new += _arc(c, r, q1, v)
            new += _arc(c, r, v, q2)[1:]
            new.append(q2)
        out.append(np.array(new))
    return out


def _arc(c, r, p, q, step_deg: float = 15.0):
    t0 = math.atan2(p[1] - c[1], p[0] - c[0])
    t1 = math.atan2(q[1] - c[1], q[0] - c[0])
    d = (t1 - t0 + math.pi) % (2 * math.pi) - math.pi          # kürzerer Bogen
    n = max(2, int(abs(math.degrees(d)) / step_deg) + 1)
    ts = t0 + d * np.linspace(0, 1, n)
    return [np.array([c[0] + r * math.cos(t), c[1] + r * math.sin(t)]) for t in ts]


def sides(rings_a, rings_b, tol: float = 0.5, min_len: float = 3.0):
    """Lage der beiden Teile zueinander an der längsten gemeinsamen Kante:
    ('left'|'right'|'bottom'|'top' für A, dasselbe für B) oder None, wenn sich keine Kanten berühren."""
    ra = [np.asarray(r, float) for r in rings_a]
    rb = [np.asarray(r, float) for r in rings_b]
    edges = find_shared_edges(ra, rb, tol=tol, min_len=min_len)
    if not edges:
        return None
    e = edges[0]
    to_b = -_material_normal(ra, e.p0, e.p1)            # von A zur Kante hinaus, also Richtung B
    if abs(to_b[0]) >= abs(to_b[1]):
        return ("left", "right") if to_b[0] > 0 else ("right", "left")
    return ("bottom", "top") if to_b[1] > 0 else ("top", "bottom")


def finger_joint(rings_a, rings_b, thickness: float, finger: float = 10.0, kind: str = "tabs",
                 dogbone: float = 0.0, tol: float = 0.5, all_edges: bool = False):
    """Zinken an der längsten gemeinsamen Kante (oder allen). Gibt neue Ringe für A und B und
    die Anzahl der bearbeiteten Kanten zurück."""
    if thickness <= 0:
        raise ValueError("Materialstärke muss größer als 0 sein.")
    ra = [np.asarray(r, float).copy() for r in rings_a]
    rb = [np.asarray(r, float).copy() for r in rings_b]
    edges = find_shared_edges(ra, rb, tol=tol, min_len=max(3 * thickness, finger))
    if not edges:
        raise ValueError("Keine gemeinsame Kante gefunden. Die beiden Teile so hinlegen, dass sich die zu "
                         "verbindenden Kanten berühren (z. B. mit Anordnen → Ausrichten), dann erneut versuchen.")
    if not all_edges:
        edges = edges[:1]
    # pro Ring von hinten nach vorn bearbeiten, damit die Segmentnummern stimmen
    corners_a, corners_b = [], []
    for e in sorted(edges, key=lambda e: -e.ka):
        cnt = finger_count(e.length, finger)
        n_a = _material_normal(ra, e.p0, e.p1)
        if kind == "tabs":
            da = [-thickness if i % 2 == 0 else 0.0 for i in range(cnt)]
        else:
            da = [0.0 if i % 2 == 0 else thickness for i in range(cnt)]
        new = _apply_profile(ra[e.ra], e.ka, e.p0, e.p1, da, n_a)
        corners_a += [p for p in new if not any(np.allclose(p, q) for q in ra[e.ra])]
        ra[e.ra] = new
    for e in sorted(edges, key=lambda e: -e.kb):
        cnt = finger_count(e.length, finger)
        n_b = _material_normal(rb, e.p0, e.p1)
        db = [thickness if i % 2 == 0 else 0.0 for i in range(cnt)]
        # Teil B kann die Kante in Gegenrichtung durchlaufen – Profil an p0/p1 ausrichten
        seg0 = rb[e.rb][e.kb]
        seg1 = rb[e.rb][(e.kb + 1) % len(rb[e.rb])]
        u = seg1 - seg0
        p0, p1 = (e.p0, e.p1) if (e.p1 - e.p0) @ u >= 0 else (e.p1, e.p0)
        dd = db if (e.p1 - e.p0) @ u >= 0 else db[::-1]
        new = _apply_profile(rb[e.rb], e.kb, p0, p1, dd, n_b)
        corners_b += [p for p in new if not any(np.allclose(p, q) for q in rb[e.rb])]
        rb[e.rb] = new
    if dogbone > 0:
        ra = add_dogbones(ra, dogbone, corners_a)
        rb = add_dogbones(rb, dogbone, corners_b)
    return ra, rb, len(edges)
