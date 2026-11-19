"""Gemeinsame Kanten: doppelte Linien beim Schneiden auslassen und Teile bündig zusammenschieben.

* ``remove_overlaps`` – deckungsgleiche (übereinanderliegende) Linienstücke nur einmal behalten.
  Wird erst bei der G-Code-Erzeugung angewendet; die Konturen im Projekt bleiben vollständig.
* ``slide_distance`` – wie weit sich eine Konturmenge in eine Richtung verschieben lässt, bis sie
  eine andere berührt (für das bündige Zusammenschieben beim Nesting mit Abstand 0).
"""
from __future__ import annotations

import math
from collections import defaultdict

import numpy as np

from . import geometry as g


# --------------------------------------------------------------------------- doppelte Linien
def _cells(p, q, cell):
    """Rasterzellen entlang der Strecke p–q (mit Nachbarn, damit Toleranzen greifen)."""
    n = max(1, int(math.hypot(q[0] - p[0], q[1] - p[1]) / (cell / 2)) + 1)
    out = set()
    for t in np.linspace(0.0, 1.0, n + 1):
        cx = int(math.floor((p[0] + (q[0] - p[0]) * t) / cell))
        cy = int(math.floor((p[1] + (q[1] - p[1]) * t) / cell))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                out.add((cx + dx, cy + dy))
    return out


def remove_overlaps(paths: list[g.Polyline], tol: float = 0.02, cell: float = 2.0) -> tuple[list[g.Polyline], float]:
    """Linienstücke, die genau auf schon vorhandenen liegen (Abstand ≤ tol, parallel), entfernen.

    Rückgabe: (Pfade, eingesparte Länge in mm). Pfade ohne Überdeckung bleiben unverändert erhalten.
    """
    grid: dict = defaultdict(list)
    segs: list[tuple[np.ndarray, np.ndarray]] = []
    out: list[g.Polyline] = []
    saved = 0.0
    for path in paths:
        lp = path.loop_pts()
        pieces = []                      # (Segmentindex, t0, t1) der verbleibenden Stücke
        changed = False
        new_segs = []
        for k in range(len(lp) - 1):
            p, q = lp[k], lp[k + 1]
            d = q - p
            L = float(math.hypot(d[0], d[1]))
            if L <= 1e-12:
                continue
            u = d / L
            cov = []
            seen = set()
            for c in _cells(p, q, cell):
                for si in grid.get(c, ()):
                    if si in seen:
                        continue
                    seen.add(si)
                    a, b = segs[si]
                    # beide Enden nahe an der Geraden p–q?
                    if abs(u[0] * (a[1] - p[1]) - u[1] * (a[0] - p[0])) > tol:
                        continue
                    if abs(u[0] * (b[1] - p[1]) - u[1] * (b[0] - p[0])) > tol:
                        continue
                    ta = float(u[0] * (a[0] - p[0]) + u[1] * (a[1] - p[1]))
                    tb = float(u[0] * (b[0] - p[0]) + u[1] * (b[1] - p[1]))
                    lo, hi = max(0.0, min(ta, tb)), min(L, max(ta, tb))
                    if hi - lo > tol:
                        cov.append((lo, hi))
            new_segs.append((p.copy(), q.copy()))
            # freie Teilstücke = [0, L] minus Überdeckung
            free = []
            cur = 0.0
            for lo, hi in sorted(cov):
                if lo > cur + tol:
                    free.append((cur, lo))
                cur = max(cur, hi)
            if cur < L - tol:
                free.append((cur, L))
            if not cov:
                free = [(0.0, L)]
            if free != [(0.0, L)]:
                changed = True
                saved += L - sum(b - a for a, b in free)
            for a, b in free:
                pieces.append((k, a / L, b / L))
        # eigene Strecken erst nach dem Pfad eintragen (ein Pfad überdeckt sich nicht selbst)
        for p, q in new_segs:
            si = len(segs)
            segs.append((p, q))
            for c in _cells(p, q, cell):
                grid[c].append(si)
        if not changed:
            out.append(path)
            continue
        # Stücke zu zusammenhängenden offenen Pfaden verbinden
        chains: list[list[np.ndarray]] = []
        last = None
        for k, t0, t1 in pieces:
            p, q = lp[k], lp[k + 1]
            a, b = p + (q - p) * t0, p + (q - p) * t1
            if last is not None and last[0] == k - 1 and last[1] >= 1 - 1e-9 and t0 <= 1e-9:
                chains[-1].append(b)
            else:
                chains.append([a, b])
            last = (k, t1)
        if (path.closed and len(chains) > 1 and pieces and pieces[0][0] == 0 and pieces[0][1] <= 1e-9
                and pieces[-1][0] == len(lp) - 2 and pieces[-1][2] >= 1 - 1e-9):
            chains[0] = chains[-1][:-1] + chains[0]   # über den Startpunkt hinweg verbinden
            chains.pop()
        for c in chains:
            if len(c) >= 2:
                piece = g.Polyline(np.array(c), False)
                piece._order = path._order
                out.append(piece)
    return out, saved


# --------------------------------------------------------------------------- Zusammenschieben
def _edges(polys) -> tuple[np.ndarray, np.ndarray]:
    a, b = [], []
    for p in polys:
        lp = p.loop_pts()
        if len(lp) >= 2:
            a.append(lp[:-1])
            b.append(lp[1:])
    if not a:
        return np.zeros((0, 2)), np.zeros((0, 2))
    return np.vstack(a), np.vstack(b)


def slide_distance(moving, fixed, direction, tol: float = 1e-6) -> float:
    """Weg, um den sich 'moving' (Polylinien) in 'direction' verschieben lässt, bis es 'fixed' berührt.

    Kanten, die sich quer zur Fahrtrichtung überlappen, werden paarweise verglichen; Kanten, die sich
    nur in einem Punkt treffen (Vorbeigleiten an einer Ecke), blockieren nicht. inf = kein Hindernis.
    """
    d = np.asarray(direction, float)
    d = d / np.hypot(*d)
    n = np.array([-d[1], d[0]])
    A0, A1 = _edges(moving)
    B0, B1 = _edges(fixed)
    if not len(A0) or not len(B0):
        return math.inf
    # Koordinaten: s quer zur Fahrt, a entlang der Fahrt
    sa0, sa1, aa0, aa1 = A0 @ n, A1 @ n, A0 @ d, A1 @ d
    sb0, sb1, ab0, ab1 = B0 @ n, B1 @ n, B0 @ d, B1 @ d
    keepA = np.abs(sa1 - sa0) > 1e-9                     # Kanten in Fahrtrichtung zählen nicht
    keepB = np.abs(sb1 - sb0) > 1e-9
    sa0, sa1, aa0, aa1 = sa0[keepA], sa1[keepA], aa0[keepA], aa1[keepA]
    sb0, sb1, ab0, ab1 = sb0[keepB], sb1[keepB], ab0[keepB], ab1[keepB]
    if not len(sa0) or not len(sb0):
        return math.inf
    best = math.inf
    loA, hiA = np.minimum(sa0, sa1), np.maximum(sa0, sa1)
    loB, hiB = np.minimum(sb0, sb1), np.maximum(sb0, sb1)
    step = max(1, 4_000_000 // max(1, len(sb0)))
    for i in range(0, len(sa0), step):
        sl = slice(i, i + step)
        lo = np.maximum(loA[sl, None], loB[None, :])
        hi = np.minimum(hiA[sl, None], hiB[None, :])
        ok = hi - lo > 1e-6
        if not ok.any():
            continue

        def at(s, s0, s1, a0, a1):
            return a0 + (a1 - a0) * (s - s0) / (s1 - s0)
        ga0 = at(lo, sb0[None, :], sb1[None, :], ab0[None, :], ab1[None, :]) - \
            at(lo, sa0[sl, None], sa1[sl, None], aa0[sl, None], aa1[sl, None])
        ga1 = at(hi, sb0[None, :], sb1[None, :], ab0[None, :], ab1[None, :]) - \
            at(hi, sa0[sl, None], sa1[sl, None], aa0[sl, None], aa1[sl, None])
        ahead = ok & (ga0 >= -tol) & (ga1 >= -tol)
        if ahead.any():
            best = min(best, float(np.minimum(ga0, ga1)[ahead].min()))
    return max(0.0, best)


def item_polys(it) -> list[g.Polyline]:
    if it.kind == "raster":
        return [g.Polyline(g.apply_matrix(it.matrix(), it.local_corners()), True)]
    return it.world_paths()


def compact(items, x_min: float, y_min: float, rounds: int = 3) -> float:
    """Teile nach links und unten schieben, bis sie sich berühren (gemeinsame Kanten).

    x_min/y_min: linke/untere Grenze der Grundfläche (inkl. Rand). Rückgabe: gesamter Verschiebeweg.
    """
    total = 0.0
    for _ in range(rounds):
        moved = 0.0
        order = sorted(items, key=lambda it: (it.world_bbox()[0] + it.world_bbox()[1]))
        for it in order:
            for axis, d in ((0, (-1.0, 0.0)), (1, (0.0, -1.0))):
                bb = it.world_bbox()
                limit = bb[axis] - (x_min if axis == 0 else y_min)
                if limit <= 1e-6:
                    continue
                mine = item_polys(it)
                others = []
                for o in items:
                    if o is it:
                        continue
                    ob = o.world_bbox()
                    # nur Teile, die quer zur Fahrt überlappen und davor liegen
                    if axis == 0 and (ob[1] >= bb[3] or ob[3] <= bb[1] or ob[0] >= bb[2]):
                        continue
                    if axis == 1 and (ob[0] >= bb[2] or ob[2] <= bb[0] or ob[1] >= bb[3]):
                        continue
                    others += item_polys(o)
                t = min(limit, slide_distance(mine, others, d) if others else math.inf)
                if t > 1e-4:
                    if axis == 0:
                        it.x -= t
                    else:
                        it.y -= t
                    moved += t
        total += moved
        if moved < 1e-3:
            break
    return total
