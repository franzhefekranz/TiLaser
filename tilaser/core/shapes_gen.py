"""Weitere Generatoren: Zahnräder (Evolvente), Puzzle und Schablonen-Stege."""
from __future__ import annotations

import math
import random

import numpy as np

from . import geometry as g
from .clip import boolean


# --------------------------------------------------------------------------- Zahnrad
def _inv(a: float) -> float:
    return math.tan(a) - a


def spur_gear(module: float, teeth: int, pressure_deg: float = 20.0, bore: float = 0.0,
              backlash: float = 0.0, cx: float = 0.0, cy: float = 0.0, rotation: float = 0.0,
              steps: int = 8) -> list[g.Polyline]:
    """Stirnrad mit Evolventenverzahnung (ohne Unterschnitt-Korrektur). Maße in mm.
    backlash: Flankenspiel am Teilkreis (mm), wird von der Zahndicke abgezogen."""
    z = int(teeth)
    if z < 6 or module <= 0:
        raise ValueError("Mindestens 6 Zähne und ein Modul größer als 0.")
    a = math.radians(pressure_deg)
    r = module * z / 2
    rb = r * math.cos(a)
    ra = r + module
    rf = max(0.1, r - 1.25 * module)
    half = math.pi / (2 * z) - backlash / (2 * r)          # halbe Zahndicke am Teilkreis (Winkel)

    def phi(rr):                                            # halbe Zahnbreite (Winkel) im Radius rr
        rr = max(rr, rb)
        return half + _inv(a) - _inv(math.acos(rb / rr))

    r_lo = max(rf, rb)
    rs = np.linspace(r_lo, ra, steps + 1)
    ph = np.array([phi(x) for x in rs])
    ok = ph > 0
    if not ok.all():                                        # spitze Zähne: am Schnittpunkt abschneiden
        rs, ph = rs[ok], ph[ok]
    pts = []
    pitch = 2 * math.pi / z
    rot = math.radians(rotation)
    for k in range(z):
        c = k * pitch + rot
        flank_l = [(rr, c - p) for rr, p in zip(rs, ph)]
        flank_r = [(rr, c + p) for rr, p in zip(rs[::-1], ph[::-1])]
        seq = []
        if rf < rb:
            seq.append((rf, c - ph[0]))
        seq += flank_l
        tip0, tip1 = c - ph[-1], c + ph[-1]
        nt = max(1, int((tip1 - tip0) / math.radians(3)))
        seq += [(rs[-1], tip0 + (tip1 - tip0) * i / nt) for i in range(1, nt)]
        seq += flank_r
        if rf < rb:
            seq.append((rf, c + ph[0]))
        # Fußkreis bis zum nächsten Zahn
        r0 = c + ph[0]
        r1 = c + pitch - ph[0]
        nr = max(1, int((r1 - r0) / math.radians(4)))
        seq += [(rf, r0 + (r1 - r0) * i / nr) for i in range(1, nr)]
        pts += seq
    outline = np.array([[cx + rr * math.cos(t), cy + rr * math.sin(t)] for rr, t in pts])
    out = [g.Polyline(outline, True)]
    if bore > 0:
        if bore / 2 >= rf - 0.5:
            raise ValueError("Die Bohrung ist zu groß für dieses Zahnrad.")
        out.append(g.Polyline(g.circle_points(cx, cy, bore / 2), True))
    return out


def gear_info(module: float, teeth: int) -> dict:
    r = module * teeth / 2
    return {"pitch_d": 2 * r, "outer_d": 2 * (r + module), "root_d": 2 * (r - 1.25 * module)}


def gear_pair(module: float, z1: int, z2: int, pressure_deg: float = 20.0, bore1: float = 0.0,
              bore2: float = 0.0, backlash: float = 0.1, cx: float = 0.0, cy: float = 0.0):
    """Zwei kämmende Räder nebeneinander (Achsabstand m·(z1+z2)/2)."""
    dist = module * (z1 + z2) / 2
    a = spur_gear(module, z1, pressure_deg, bore1, backlash, cx, cy)
    rot2 = 180.0 + (180.0 / z2 if z2 % 2 == 0 else 0.0)
    b = spur_gear(module, z2, pressure_deg, bore2, backlash, cx + dist, cy, rotation=rot2)
    return a, b, dist


# --------------------------------------------------------------------------- Puzzle
def jigsaw(width: float, height: float, cols: int, rows: int, tab: float = 20.0, jitter: float = 4.0,
           seed: int = 1, radius: float = 0.0, x0: float = 0.0, y0: float = 0.0):
    """Klassisches Puzzle (Verfahren nach Draradechs Jigsaw-Generator).
    tab: Nasengröße in % der Teilegröße, jitter: Zufallsabweichung in %.
    Gibt (Rahmen als geschlossene Kontur, innere Schnittlinien als offene Pfade) zurück."""
    if cols < 1 or rows < 1 or width <= 0 or height <= 0:
        raise ValueError("Breite, Höhe, Spalten und Reihen müssen größer als 0 sein.")
    rnd = random.Random(int(seed))
    t = tab / 200.0
    j = jitter / 100.0
    st = {}

    def uni(lo, hi):
        return lo + rnd.random() * (hi - lo)

    def first():
        st["e"] = uni(-j, j)
        st["flip"] = rnd.random() > 0.5
        nxt()

    def nxt():
        old = st.get("flip", False)
        st["flip"] = rnd.random() > 0.5
        st["a"] = -st["e"] if st["flip"] == old else st["e"]
        st["b"], st["c"], st["d"], st["e"] = uni(-j, j), uni(-j, j), uni(-j, j), uni(-j, j)

    lines = []

    def edge(vertical, xi, yi, xn, yn):
        sl = height / yn if vertical else width / xn          # Länge in Laufrichtung
        sw = width / xn if vertical else height / yn          # Abstand quer
        ol = sl * (yi if vertical else xi)
        ow = sw * (xi if vertical else yi)
        s = -1.0 if st["flip"] else 1.0
        a, b, c, d, e = st["a"], st["b"], st["c"], st["d"], st["e"]

        def P(lv, wv):
            L = ol + sl * lv
            W = ow + sw * wv * s
            return np.array([x0 + W, y0 + L]) if vertical else np.array([x0 + L, y0 + W])
        p = [P(0, 0), P(0.2, a), P(0.5 + b + d, -t + c), P(0.5 - t + b, t + c),
             P(0.5 - 2 * t + b - d, 3 * t + c), P(0.5 + 2 * t + b - d, 3 * t + c), P(0.5 + t + b, t + c),
             P(0.5 + b + d, -t + c), P(0.8, e), P(1.0, 0)]
        seg = []
        for k in range(3):
            q = g.flatten_cubic(p[3 * k], p[3 * k + 1], p[3 * k + 2], p[3 * k + 3], 0.05)
            seg.append(q if not seg else q[1:])
        return np.vstack(seg)

    for yi in range(1, rows):                                   # waagerechte Linien
        first()
        pts: list = []
        for xi in range(cols):
            e = edge(False, xi, yi, cols, rows)
            pts.append(e if not pts else e[1:])
            nxt()
        lines.append(g.Polyline(np.vstack(pts), False))
    for xi in range(1, cols):                                   # senkrechte Linien
        first()
        pts = []
        for yi in range(rows):
            e = edge(True, xi, yi, cols, rows)
            pts.append(e if not pts else e[1:])
            nxt()
        lines.append(g.Polyline(np.vstack(pts), False))
    if radius > 0:
        rr = min(radius, width / 2, height / 2)
        frame = _rounded_rect(x0, y0, width, height, rr)
    else:
        frame = np.array([[x0, y0], [x0 + width, y0], [x0 + width, y0 + height], [x0, y0 + height]])
    return g.Polyline(frame, True), lines


def _rounded_rect(x, y, w, h, r):
    pts = []
    for cx, cy, a0 in ((x + w - r, y + r, -90), (x + w - r, y + h - r, 0), (x + r, y + h - r, 90), (x + r, y + r, 180)):
        for k in range(0, 18):
            a = math.radians(a0 + 5 * k)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return np.array(pts)


# --------------------------------------------------------------------------- Schablone
def stencil_bridges(polys: list[g.Polyline], width: float = 1.5, direction: str = "vertical") -> tuple[list[g.Polyline], int]:
    """Stege einfügen, damit Innenteile (z. B. im O, A, B) beim Ausschneiden nicht herausfallen.
    Für jedes Loch wird ein Streifen der Breite `width` durch seine Mitte über die ganze
    umgebende Form gelegt und abgezogen. Rückgabe: (neue Konturen, Anzahl Stege)."""
    closed = [p for p in polys if p.closed and len(p.pts) >= 3]
    if not closed:
        raise ValueError("Keine geschlossenen Konturen ausgewählt.")
    depth = g.nesting_depth(closed)
    strips = []
    for i, (p, dep) in enumerate(zip(closed, depth)):
        if dep % 2 == 0:
            continue
        # umgebende Außenkontur = kleinste gerade Ebene, die das Loch enthält
        parents = [q for q, dq in zip(closed, depth) if dq == dep - 1 and
                   g.points_in_polygon(p.pts[:1], q.pts)[0]]
        if not parents:
            continue
        par = min(parents, key=lambda q: _area(q.pts))
        bb_h = g.bbox_of([p])
        bb_p = g.bbox_of([par])
        jit = 1.7e-4
        if direction == "vertical":
            mx = (bb_h[0] + bb_h[2]) / 2 + jit
            strips.append(np.array([[mx - width / 2, bb_p[1] - 1], [mx + width / 2, bb_p[1] - 1],
                                    [mx + width / 2, bb_p[3] + 1], [mx - width / 2, bb_p[3] + 1]]))
        else:
            my = (bb_h[1] + bb_h[3]) / 2 + jit
            strips.append(np.array([[bb_p[0] - 1, my - width / 2], [bb_p[2] + 1, my - width / 2],
                                    [bb_p[2] + 1, my + width / 2], [bb_p[0] - 1, my + width / 2]]))
    if not strips:
        return [p.copy() for p in closed], 0
    # überlappende Streifen (z. B. B mit zwei Löchern übereinander) zu einem Rechteck zusammenfassen
    rects = [(r[:, 0].min(), r[:, 1].min(), r[:, 0].max(), r[:, 1].max()) for r in strips]
    merged: list = []
    for rc in sorted(rects):
        for k, m in enumerate(merged):
            if rc[0] <= m[2] and rc[2] >= m[0] and rc[1] <= m[3] and rc[3] >= m[1]:
                merged[k] = (min(m[0], rc[0]), min(m[1], rc[1]), max(m[2], rc[2]), max(m[3], rc[3]))
                break
        else:
            merged.append(rc)
    rings = [g.Polyline(np.array([[a, b], [c, b], [c, d], [a, d]]), True) for a, b, c, d in merged]
    return boolean(closed, rings, "difference"), len(merged)


def _area(p):
    return abs(0.5 * float(np.sum(p[:, 0] * np.roll(p[:, 1], -1) - p[:, 1] * np.roll(p[:, 0], -1))))
