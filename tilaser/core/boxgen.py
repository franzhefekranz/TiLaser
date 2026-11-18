"""Box-Generator: Kasten mit Fingerzinken aus Plattenmaterial.

Koordinaten: Breite = X, Tiefe = Y, Höhe = Z. Jede Platte ist ein Rechteck in Außenmaßen, an
dessen Kanten ein Streifen von Materialstärke t im Nachbarbrett liegt. In diesem Streifen
wechseln sich Zinken (Material bleibt) und Aussparungen ab, beim Nachbarn genau umgekehrt.
Die Eckwürfel (t × t × t) gehören immer der Platte mit der höchsten Rangfolge
(Boden/Deckel > Vorne/Hinten > Links/Rechts), so ist jeder Teil der Wand genau einmal vorhanden.

Der Schnittbreitenausgleich (Kerf) wird nicht in die Geometrie eingerechnet, sondern als
Kerf-Wert der erzeugten Ebene gesetzt; TiLaser versetzt die Konturen beim Ausgeben.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import geometry as g
from .model import Item, Project, VectorPath, new_id
from .text_import import default_font, text_to_polylines

PRIO = {"Boden": 3, "Deckel": 3, "Vorne": 2, "Hinten": 2, "Links": 1, "Rechts": 1}


@dataclass
class BoxSpec:
    width: float = 100.0          # X
    depth: float = 80.0           # Y
    height: float = 50.0          # Z
    inner: bool = True            # Maße innen (sonst außen)
    thickness: float = 3.0
    finger: float = 10.0          # gewünschte Zinkenbreite
    closed: bool = True           # mit Deckel
    kerf: float = 0.1             # Schnittbreite, wird als Ebenen-Kerf gesetzt
    gap: float = 4.0              # Abstand der Teile im Layout
    x: float = 10.0
    y: float = 10.0
    labels: bool = True
    power: float = 100.0
    speed: float = 300.0
    passes: int = 1
    label_power: float = 15.0
    label_speed: float = 3000.0

    def outer(self):
        t = self.thickness
        if not self.inner:
            return self.width, self.depth, self.height
        return self.width + 2 * t, self.depth + 2 * t, self.height + (2 if self.closed else 1) * t

    def check(self):
        t = self.thickness
        W, D, H = self.outer()
        if t <= 0:
            raise ValueError("Materialstärke muss größer als 0 sein.")
        if min(W, D, H) <= 3 * t:
            raise ValueError("Die Box ist zu klein für diese Materialstärke "
                             f"(jedes Außenmaß muss größer als {3 * t:g} mm sein).")
        if self.finger < t:
            raise ValueError("Die Zinkenbreite sollte mindestens so groß wie die Materialstärke sein.")


def finger_count(length: float, t: float, finger: float) -> int:
    """Ungerade Anzahl Abschnitte auf dem Kantenstück zwischen den Ecken."""
    n = max(1, int(round((length - 2 * t) / max(finger, 1e-6))))
    if n % 2 == 0:
        n = n + 1 if (length - 2 * t) / (n + 1) >= t else max(1, n - 1)
    return n


def panels(spec: BoxSpec):
    """Liste (Name, Breite, Höhe, [Nachbar unten, rechts, oben, links])."""
    W, D, H = spec.outer()
    top = "Deckel" if spec.closed else None
    out = [("Boden", W, D, ["Vorne", "Rechts", "Hinten", "Links"])]
    if spec.closed:
        out.append(("Deckel", W, D, ["Vorne", "Rechts", "Hinten", "Links"]))
    out += [("Vorne", W, H, ["Boden", "Rechts", top, "Links"]),
            ("Hinten", W, H, ["Boden", "Links", top, "Rechts"]),
            ("Links", D, H, ["Boden", "Vorne", top, "Hinten"]),
            ("Rechts", D, H, ["Boden", "Hinten", top, "Vorne"])]
    return out


def panel_outline(name: str, a: float, b: float, neighbors, t: float, finger: float) -> np.ndarray:
    """Umriss einer Platte (a × b, Ursprung links unten), gegen den Uhrzeigersinn."""
    corners = [np.array(p, float) for p in ((0, 0), (a, 0), (a, b), (0, b))]
    me = PRIO[name]

    def filled(k):          # Ecke k zwischen Kante k-1 und Kante k
        return all(n is None or me > PRIO[n] for n in (neighbors[k - 1], neighbors[k]))

    pts = []
    for k in range(4):
        p0, p1 = corners[k], corners[(k + 1) % 4]
        L = float(np.hypot(*(p1 - p0)))
        u = (p1 - p0) / L
        n = np.array([-u[1], u[0]])                  # nach innen (Umlauf gegen den Uhrzeigersinn)

        def pt(s, d):
            return p0 + u * s + n * d
        if filled(k):
            pts += [pt(0, t), pt(0, 0), pt(t, 0)]
        else:
            pts.append(pt(t, t))
        nb = neighbors[k]
        if nb is None:
            pts += [pt(t, 0), pt(L - t, 0)]
            continue
        cnt = finger_count(L, t, finger)
        w = (L - 2 * t) / cnt
        even_tabs = me > PRIO[nb]
        for i in range(cnt):
            tab = (i % 2 == 0) == even_tabs
            d = 0.0 if tab else t
            pts += [pt(t + i * w, d), pt(t + (i + 1) * w, d)]
    return _clean(np.array(pts))


def _clean(p: np.ndarray, eps=1e-9) -> np.ndarray:
    keep = [q for i, q in enumerate(p) if i == 0 or np.hypot(*(q - p[i - 1])) > eps]
    if len(keep) > 1 and np.hypot(*(keep[0] - keep[-1])) <= eps:
        keep.pop()
    changed = True
    while changed and len(keep) > 3:
        changed = False
        for i in range(len(keep)):
            a, b, c = keep[i - 1], keep[i], keep[(i + 1) % len(keep)]
            cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
            if abs(cross) < 1e-9:
                keep.pop(i)
                changed = True
                break
    return np.array(keep)


def polygon_area(p: np.ndarray) -> float:
    x, y = p[:, 0], p[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def build_box(spec: BoxSpec) -> Project:
    """Projekt mit allen Platten nebeneinander (zwei Spalten) und optionaler Beschriftung."""
    spec.check()
    t = spec.thickness
    p = Project()
    lab = None
    if spec.labels:
        lab = p.add_layer("Box-Beschriftung", mode="cut")
        lab.power, lab.speed = spec.label_power, spec.label_speed
    cut = p.add_layer("Box", mode="cut")
    cut.power, cut.speed, cut.passes, cut.kerf = spec.power, spec.speed, max(1, spec.passes), spec.kerf
    font = default_font() if lab else None
    parts = panels(spec)
    col_w = max(a for _, a, _, _ in parts)
    y = spec.y
    for row in range(0, len(parts), 2):
        pair = parts[row:row + 2]
        x = spec.x
        for name, a, b, nb in pair:
            poly = g.Polyline(panel_outline(name, a, b, nb, t, spec.finger) + [x, y], True)
            cx, cy = x + a / 2, y + b / 2
            it = Item(id=new_id("I"), name=f"Box {name}", kind="vector", x=cx, y=cy)
            it.paths = [VectorPath(poly.transformed(g.translate(-cx, -cy)), cut.id)]
            if font:
                size = max(3.0, min(8.0, min(a, b) / 6))
                txt = text_to_polylines(name, font, size)
                if txt:
                    bb = g.bbox(txt)
                    m = g.translate(-(bb[0] + bb[2]) / 2, -(bb[1] + bb[3]) / 2)
                    it.paths += [VectorPath(q.transformed(m), lab.id) for q in txt]
            p.items.append(it)
            x += col_w + spec.gap
        y += max(b for _, _, b, _ in pair) + spec.gap
    return p
