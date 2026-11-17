"""Nesting: Objekte platzsparend auf einer rechteckigen Grundfläche anordnen.

Verfahren (Raster-Nesting):
- Jedes Teil wird in ein Belegungsraster umgewandelt (Zellgröße ≈ 0,3–2 mm, konservativ:
  jede berührte Zelle zählt). Löcher bleiben frei, kleine Teile können also in Löcher
  größerer Teile gesetzt werden.
- Der Mindestabstand wird durch Aufweiten der Teilmaske um den Abstand berücksichtigt.
- Für jede erlaubte Drehung werden mit einer FFT-Korrelation alle kollisionsfreien
  Positionen auf einmal bestimmt; gewählt wird die Lage mit der niedrigsten Oberkante,
  dann der kleinsten rechten Kante (unten links beginnend).
- Größte Teile zuerst.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from . import geometry as g
from .editing import clone_item
from .model import Item, Project


@dataclass
class NestSettings:
    x: float = 0.0
    y: float = 0.0
    width: float = 400.0
    height: float = 400.0
    spacing: float = 2.0          # Mindestabstand zwischen Teilen (mm)
    margin: float = 2.0           # Abstand zum Rand der Grundfläche (mm)
    rotations: tuple = (0, 90, 180, 270)
    copies: int = 1               # Anzahl je ausgewähltem Objekt
    cell: float | None = None     # Rastergröße (mm), None = automatisch
    use_holes: bool = True        # kleine Teile in Löcher größerer Teile setzen


@dataclass
class NestResult:
    placed: list[str] = field(default_factory=list)
    unplaced: list[str] = field(default_factory=list)
    new_items: list[Item] = field(default_factory=list)   # Kopien, die hinzugefügt wurden
    utilization: float = 0.0                             # belegte Fläche / Grundfläche
    cell: float = 0.0


# --------------------------------------------------------------------------- Rasterisierung
def _world_polys(it: Item, extra_rot: float) -> list[g.Polyline]:
    r0 = it.rotation
    it.rotation = r0 + extra_rot
    try:
        if it.kind == "raster":
            c = g.apply_matrix(it.matrix(), it.local_corners())
            return [g.Polyline(c, True)]
        return it.world_paths()
    finally:
        it.rotation = r0


def rasterize(polys: list[g.Polyline], cell: float, fill_holes: bool = False):
    """Konservative Belegungsmaske. Rückgabe: (Maske[Zeile=y, Spalte=x], x0, y0)."""
    pts = np.vstack([p.pts for p in polys if len(p.pts)])
    x0, y0 = pts.min(axis=0)
    x1, y1 = pts.max(axis=0)
    cols = max(1, int(math.floor((x1 - x0) / cell)) + 1)
    rows = max(1, int(math.floor((y1 - y0) / cell)) + 1)
    mask = np.zeros((rows, cols), bool)
    # Flächen (Even-Odd) zeilenweise mit drei Abtastlinien je Zelle
    closed = [p for p in polys if p.closed and len(p.pts) >= 3]
    if fill_holes and closed:                    # nur Außenkonturen füllen
        depth = g.nesting_depth(closed)
        closed = [p for p, d in zip(closed, depth) if d % 2 == 0]
    if closed:
        A = np.vstack([p.pts for p in closed])
        B = np.vstack([np.roll(p.pts, -1, axis=0) for p in closed])
        keep = np.abs(A[:, 1] - B[:, 1]) > 1e-12
        A, B = A[keep], B[keep]
        lo, hi = np.minimum(A[:, 1], B[:, 1]), np.maximum(A[:, 1], B[:, 1])
        slope = (B[:, 0] - A[:, 0]) / (B[:, 1] - A[:, 1])
        for j in range(rows):
            for f in (0.02, 0.5, 0.98):
                yy = y0 + (j + f) * cell
                sel = (lo <= yy) & (hi > yy)
                if not sel.any():
                    continue
                xs = np.sort(A[sel, 0] + (yy - A[sel, 1]) * slope[sel])
                for a, b in xs[: len(xs) // 2 * 2].reshape(-1, 2):
                    ca = int(math.floor((a - x0) / cell))
                    cb = int(math.floor((b - x0) / cell))
                    mask[j, max(0, ca):min(cols, cb + 1)] = True
    # Konturen (auch offene Linien) entlang abtasten
    for p in polys:
        lp = p.loop_pts()
        for a, b in zip(lp[:-1], lp[1:]):
            n = max(2, int(math.ceil(np.hypot(*(b - a)) / (cell / 3))) + 1)
            t = np.linspace(0, 1, n)[:, None]
            q = a + (b - a) * t
            ci = np.clip(((q[:, 0] - x0) / cell).astype(int), 0, cols - 1)
            ri = np.clip(((q[:, 1] - y0) / cell).astype(int), 0, rows - 1)
            mask[ri, ci] = True
    return mask, float(x0), float(y0)


def dilate(mask: np.ndarray, r: int) -> np.ndarray:
    """Aufweiten um r Zellen (kreisförmig). Ergebnis ist um r Zellen je Seite größer."""
    if r <= 0:
        return mask.copy()
    out = np.zeros((mask.shape[0] + 2 * r, mask.shape[1] + 2 * r), bool)
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dx * dx + dy * dy <= r * r + r:          # etwas großzügiger als der Kreis
                out[r + dy:r + dy + mask.shape[0], r + dx:r + dx + mask.shape[1]] |= mask
    return out


# --------------------------------------------------------------------------- Nesting
SCORES = ("bottom_left", "top_edge", "right_edge")
ORDERS = ("area", "max_side", "height")


def _place_all(order_idx, score_mode, shapes, keys, rotations, G_rows, G_cols, m, r, progress=None, fp_cache=None):
    """Platziert die Teile in der gegebenen Reihenfolge. Rückgabe: (Platzierungen, belegte Zellen, Hüllhöhe)."""
    occ = np.zeros((G_rows, G_cols), bool)
    placements = {}
    area = 0
    top = 0
    for n, idx in enumerate(order_idx):
        if progress:
            progress(n, len(order_idx))
        O = np.pad(occ, r).astype(np.float32)
        FO_cache = {}
        best = None
        for rot in rotations:
            mask, dmask, _, _ = shapes[keys[idx]][rot]
            ph, pw = mask.shape
            vmax, umax = G_rows - m - ph, G_cols - m - pw
            if vmax < m or umax < m:
                continue
            H = O.shape[0] + dmask.shape[0]
            W = O.shape[1] + dmask.shape[1]
            if (H, W) not in FO_cache:
                FO_cache[(H, W)] = np.fft.rfft2(O, (H, W))
            ck = (keys[idx], rot, H, W)
            if fp_cache is not None and ck in fp_cache:
                FP = fp_cache[ck]
            else:
                FP = np.fft.rfft2(dmask.astype(np.float32), (H, W))
                if fp_cache is not None:
                    fp_cache[ck] = FP
            corr = np.fft.irfft2(FO_cache[(H, W)] * np.conj(FP), (H, W))
            win = corr[m:vmax + 1, m:umax + 1]
            free = win < 0.5
            if not free.any():
                continue
            vs, us = np.nonzero(free)
            v_abs, u_abs = vs + m, us + m
            if score_mode == "bottom_left":
                score = v_abs.astype(np.int64) * (G_cols + 1) + u_abs
            elif score_mode == "top_edge":
                score = (v_abs + ph).astype(np.int64) * (G_cols + 1) + (u_abs + pw)
            else:
                score = (u_abs + pw).astype(np.int64) * (G_rows + 1) + (v_abs + ph)
            k = int(np.argmin(score))
            cand = (int(score[k]), rot, int(v_abs[k]), int(u_abs[k]))
            if best is None or cand[0] < best[0]:
                best = cand
        if best is None:
            continue
        _, rot, v, u = best
        mask = shapes[keys[idx]][rot][0]
        occ[v:v + mask.shape[0], u:u + mask.shape[1]] |= mask
        area += int(mask.sum())
        top = max(top, v + mask.shape[0])
        placements[idx] = (rot, v, u)
    return placements, area, top


def nest(project: Project, items: list[Item], s: NestSettings, progress=None, thorough: bool = True) -> NestResult:
    """Ordnet die Objekte auf der Grundfläche an. thorough=True probiert mehrere Strategien."""
    res = NestResult()
    if not items or s.width <= 0 or s.height <= 0:
        return res
    cell = s.cell or min(2.0, max(0.25, max(s.width, s.height) / 500))
    res.cell = cell
    G_rows = int(math.floor(s.height / cell))
    G_cols = int(math.floor(s.width / cell))
    m = int(math.ceil(s.margin / cell))
    r = int(math.ceil(s.spacing / cell))

    work: list[tuple[Item, bool]] = []
    keys = []
    for it in items:
        work.append((it, False))
        keys.append(id(it))
        for _ in range(max(0, s.copies - 1)):
            work.append((clone_item(it), True))
            keys.append(id(it))                   # Kopien teilen die Rastermasken des Originals
    shapes = {}
    for it in items:
        shapes[id(it)] = {}
        for rot in s.rotations:
            mask, x0, y0 = rasterize(_world_polys(it, rot), cell, fill_holes=not s.use_holes)
            shapes[id(it)][rot] = (mask, dilate(mask, r), x0, y0)

    def order(kind):
        def key(i):
            mk = shapes[keys[i]][s.rotations[0]][0]
            if kind == "area":
                return -int(mk.sum())
            if kind == "max_side":
                return -max(mk.shape)
            return -mk.shape[0]
        return sorted(range(len(work)), key=key)

    combos = [(o, sc) for o in ORDERS for sc in SCORES] if thorough else [("area", "bottom_left")]
    best = None
    fp_cache: dict = {}
    for ci, (o, sc) in enumerate(combos):
        def prog(n, total, ci=ci):
            if progress:
                progress(ci * total + n, len(combos) * total)
        pl, area, top = _place_all(order(o), sc, shapes, keys, s.rotations, G_rows, G_cols, m, r, prog, fp_cache)
        n_orig = sum(1 for i in pl if not work[i][1])
        cand = (len(pl), n_orig, -top, area)
        if best is None or cand > best[0]:
            best = (cand, pl, area)
    _, placements, area = best

    for idx, (it, is_copy) in enumerate(work):
        if idx not in placements:
            if not is_copy:
                res.unplaced.append(it.id)
            continue
        rot, v, u = placements[idx]
        _, _, x0, y0 = shapes[keys[idx]][rot]
        it.rotation += rot
        it.x += s.x + u * cell - x0
        it.y += s.y + v * cell - y0
        if is_copy:
            project.items.append(it)
            res.new_items.append(it)
        res.placed.append(it.id)
    res.utilization = area / max(1, G_rows * G_cols)
    return res
