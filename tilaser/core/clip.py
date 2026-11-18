"""Linien und Flächen an Konturen beschneiden – ohne Zusatzbibliothek.

Flächen werden wie überall in TiLaser nach der Even-Odd-Regel gelesen (Ringe, Löcher sind
einfach weitere Ringe). Boolesche Operationen arbeiten über die Ränder:

    Rand(A ∩ B) = (Rand A innerhalb B) + (Rand B innerhalb A)
    Rand(A − B) = (Rand A außerhalb B) + (Rand B innerhalb A)

Die Randstücke werden anschließend über gemeinsame Endpunkte wieder zu geschlossenen Ringen
verkettet. Das ist robust, solange Kanten von A und B nicht genau aufeinander liegen; Erzeuger
verschieben ihr Raster deshalb um einen winzigen Betrag.
"""
from __future__ import annotations

import numpy as np

from . import geometry as g

EPS = 1e-12


def _rings(polys) -> list[np.ndarray]:
    out = []
    for p in polys:
        pts = p.pts if isinstance(p, g.Polyline) else np.asarray(p, float)
        if (not isinstance(p, g.Polyline) or p.closed) and len(pts) >= 3:
            out.append(np.asarray(pts, float))
    return out


def _edges(rings) -> tuple[np.ndarray, np.ndarray]:
    if not rings:
        return np.zeros((0, 2)), np.zeros((0, 2))
    a = np.vstack(rings)
    b = np.vstack([np.roll(r, -1, axis=0) for r in rings])
    return a, b


def inside(pts: np.ndarray, rings, chunk: int = 4096) -> np.ndarray:
    """Even-Odd-Test für viele Punkte gegen alle Ringe zusammen."""
    pts = np.asarray(pts, float).reshape(-1, 2)
    c, d = _edges(_rings(rings)) if not isinstance(rings, tuple) else rings
    res = np.zeros(len(pts), bool)
    if not len(c):
        return res
    xa, ya, xb, yb = c[:, 0], c[:, 1], d[:, 0], d[:, 1]
    for k in range(0, len(pts), chunk):
        x = pts[k:k + chunk, 0][:, None]
        y = pts[k:k + chunk, 1][:, None]
        cond = (ya > y) != (yb > y)
        with np.errstate(divide="ignore", invalid="ignore"):
            xi = xa + (y - ya) * (xb - xa) / (yb - ya)
        res[k:k + chunk] = (np.sum(cond & (x < xi), axis=1) % 2) == 1
    return res


def distance_to_edges(pts: np.ndarray, rings, chunk: int = 2048) -> np.ndarray:
    """Kleinster Abstand jedes Punkts zu einer Kante der Ringe."""
    pts = np.asarray(pts, float).reshape(-1, 2)
    c, d = _edges(_rings(rings))
    out = np.full(len(pts), np.inf)
    if not len(c):
        return out
    s = d - c
    ss = np.maximum(np.einsum("ij,ij->i", s, s), EPS)
    for k in range(0, len(pts), chunk):
        p = pts[k:k + chunk][:, None, :]
        t = np.clip(np.einsum("nmj,mj->nm", p - c[None], s) / ss, 0, 1)
        q = c[None] + t[..., None] * s[None]
        out[k:k + chunk] = np.sqrt(np.min(np.sum((p - q) ** 2, axis=2), axis=1))
    return out


def split(pts: np.ndarray, closed: bool, rings, chunk: int = 512):
    """Zerlegt einen Linienzug an allen Kreuzungen mit den Ringen.
    Ergebnis: Liste (Punkte, liegt_innen)."""
    pts = np.asarray(pts, float)
    if closed:
        pts = np.vstack([pts, pts[:1]])
    if len(pts) < 2:
        return []
    c, d = _edges(_rings(rings))
    a, b = pts[:-1], pts[1:]
    r = b - a
    pieces_a, pieces_b, seg_of = [], [], []
    for k in range(0, len(a), chunk):
        ak, rk = a[k:k + chunk], r[k:k + chunk]
        if len(c):
            s = d - c
            den = rk[:, None, 0] * s[None, :, 1] - rk[:, None, 1] * s[None, :, 0]
            q = c[None] - ak[:, None]
            with np.errstate(divide="ignore", invalid="ignore"):
                t = (q[..., 0] * s[None, :, 1] - q[..., 1] * s[None, :, 0]) / den
                u = (q[..., 0] * rk[:, None, 1] - q[..., 1] * rk[:, None, 0]) / den
            ok = (np.abs(den) > EPS) & (t > 1e-9) & (t < 1 - 1e-9) & (u >= 0) & (u < 1)
        for i in range(len(ak)):
            ts = np.unique(t[i][ok[i]]) if len(c) else np.zeros(0)
            bounds = np.concatenate([[0.0], ts, [1.0]])
            for t0, t1 in zip(bounds[:-1], bounds[1:]):
                if t1 - t0 > 1e-12:
                    pieces_a.append(ak[i] + rk[i] * t0)
                    pieces_b.append(ak[i] + rk[i] * t1)
                    seg_of.append(k + i)
    if not pieces_a:
        return []
    pa, pb = np.array(pieces_a), np.array(pieces_b)
    flag = inside((pa + pb) / 2, (c, d)) if len(c) else np.zeros(len(pa), bool)
    out = []
    cur = [pa[0], pb[0]]
    cur_f = flag[0]
    for i in range(1, len(pa)):
        if flag[i] == cur_f and np.allclose(pa[i], cur[-1], atol=1e-9):
            cur.append(pb[i])
        else:
            out.append((np.array(cur), bool(cur_f)))
            cur, cur_f = [pa[i], pb[i]], flag[i]
    out.append((np.array(cur), bool(cur_f)))
    # bei geschlossenen Pfaden erstes und letztes Stück verbinden, wenn gleich
    if closed and len(out) > 1 and out[0][1] == out[-1][1]:
        merged = np.vstack([out[-1][0], out[0][0][1:]])
        out = [(merged, out[0][1])] + out[1:-1]
    return out


def clip_lines(polys, region, keep_inside: bool = True) -> list[g.Polyline]:
    """Teile der Linienzüge innerhalb (bzw. außerhalb) der Fläche als offene Pfade."""
    rings = _rings(region)
    out = []
    for p in polys:
        for pts, ins in split(p.pts, p.closed, rings):
            if ins == keep_inside and len(pts) >= 2:
                out.append(g.Polyline(pts, False))
    return out


def chain(pieces: list[np.ndarray], tol: float = 1e-6) -> list[g.Polyline]:
    """Offene Stücke über gemeinsame Endpunkte zu Ringen (bzw. Linienzügen) verketten."""
    q = max(tol, 1e-9)
    grid: dict[tuple[int, int], list[tuple[int, int]]] = {}

    def key(p):
        return int(np.floor(p[0] / q)), int(np.floor(p[1] / q))

    for i, pts in enumerate(pieces):
        for end in (0, 1):
            grid.setdefault(key(pts[0] if end == 0 else pts[-1]), []).append((i, end))
    used = [False] * len(pieces)

    def find(p, skip):
        kx, ky = key(p)
        best = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for i, end in grid.get((kx + dx, ky + dy), ()):
                    if used[i] or i == skip:
                        continue
                    e = pieces[i][0] if end == 0 else pieces[i][-1]
                    dd = abs(e[0] - p[0]) + abs(e[1] - p[1])
                    if dd <= 2 * q and (best is None or dd < best[2]):
                        best = (i, end, dd)
        return best

    out = []
    for i in range(len(pieces)):
        if used[i]:
            continue
        used[i] = True
        path = [pieces[i]]
        start = pieces[i][0]
        while True:
            tail = path[-1][-1]
            if len(path) > 1 or len(path[0]) > 2:
                if abs(tail[0] - start[0]) + abs(tail[1] - start[1]) <= 2 * q:
                    break
            nxt = find(tail, -1)
            if nxt is None:
                break
            j, end, _ = nxt
            used[j] = True
            path.append(pieces[j][1:] if end == 0 else pieces[j][::-1][1:])
        pts = np.vstack(path)
        closed = len(pts) >= 4 and abs(pts[-1][0] - pts[0][0]) + abs(pts[-1][1] - pts[0][1]) <= 2 * q
        if closed:
            pts = pts[:-1]
        if len(pts) >= 2:
            out.append(g.Polyline(_drop_collinear(pts, closed), closed))
    return out


def _drop_collinear(pts: np.ndarray, closed: bool, eps: float = 1e-9) -> np.ndarray:
    if len(pts) < 3:
        return pts
    p = pts
    prev = np.roll(p, 1, axis=0)
    nxt = np.roll(p, -1, axis=0)
    cross = (p[:, 0] - prev[:, 0]) * (nxt[:, 1] - p[:, 1]) - (p[:, 1] - prev[:, 1]) * (nxt[:, 0] - p[:, 0])
    seg = np.hypot(*(p - prev).T) * np.hypot(*(nxt - p).T)
    keep = np.abs(cross) > eps * np.maximum(seg, 1e-12)
    keep &= np.hypot(*(p - prev).T) > 1e-9
    if not closed:
        keep[0] = keep[-1] = True
    out = p[keep]
    return out if len(out) >= (3 if closed else 2) else pts


def boolean(a, b, op: str) -> list[g.Polyline]:
    """op: 'intersection' | 'difference' (a − b) | 'union'. Ergebnis: geschlossene Ringe."""
    ra, rb = _rings(a), _rings(b)
    if not ra:
        return []
    if not rb:
        return [g.Polyline(r.copy(), True) for r in ra] if op != "intersection" else []
    keep_a = {"intersection": True, "difference": False, "union": False}[op]
    keep_b = {"intersection": True, "difference": True, "union": False}[op]
    pieces = []
    for r in ra:
        pieces += [pts for pts, ins in split(r, True, rb) if ins == keep_a]
    for r in rb:
        pieces += [pts for pts, ins in split(r, True, ra) if ins == keep_b]
    return [p for p in chain(pieces) if p.closed]
